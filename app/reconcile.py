"""Reconciliation job: scan ISE for devices still on default NDGs and fix them.

Optimisations / safety valves (all configurable in Settings):
- ise_device_cache: devices that were compliant within reconcile.detail_ttl_hours
  are skipped without re-fetching their detail from ISE.
- reconcile.exclude: regex patterns (one per line) matched case-insensitively
  against the ISE device name and IP; matching devices are never touched.
- reconcile.mode = "approve": instead of writing to ISE, proposed changes are
  queued as PendingChange rows for manual approval in the GUI.
Per-device errors (e.g. ISE throttling 401s) are counted and skipped — they no
longer abort the whole run.
"""
import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone

from .clients.catalyst import CatalystClient, CatalystError
from .clients.ise import ISEClient, ISEError
from .db import SessionLocal
from .models import ReconcileRun, ISEDeviceCache, PendingChange
from .rules import is_default_ndgs, merge_ndgs
from .settings_store import get_setting, get_int
from .sync import enrich_from_cc, plan_for_device, apply_to_ise
from . import audit

log = logging.getLogger(__name__)

_running_lock = asyncio.Lock()


def _compile_excludes() -> list[re.Pattern]:
    out = []
    for line in get_setting("reconcile.exclude").replace(",", "\n").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(re.compile(line, re.IGNORECASE))
        except re.error:
            log.warning("invalid exclude pattern ignored: %r", line)
    return out


def _excluded(patterns: list[re.Pattern], *values: str) -> bool:
    return any(p.search(v) for p in patterns for v in values if v)


def _first_ip(ise_device: dict) -> str:
    ips = ise_device.get("NetworkDeviceIPList") or []
    return (ips[0] or {}).get("ipaddress", "") if ips else ""


def _update_cache(ise_id: str, name: str, ip: str, ndgs: list, now: datetime):
    with SessionLocal() as s:
        row = s.get(ISEDeviceCache, ise_id)
        if row is None:
            row = ISEDeviceCache(ise_id=ise_id)
            s.add(row)
        row.name, row.ip = name, ip
        row.ndgs = json.dumps(ndgs)
        row.has_default = is_default_ndgs(ndgs)
        row.last_detail = now
        row.last_seen = now
        s.commit()


def _queue_pending(ise_id: str, name: str, ip: str, rule_label: str,
                   old_ndgs: list, new_ndgs: list, targets: dict):
    with SessionLocal() as s:
        # supersede any older pending entry for the same device
        s.query(PendingChange).filter(PendingChange.ise_id == ise_id,
                                      PendingChange.status == "pending").delete()
        s.add(PendingChange(ise_id=ise_id, device_name=name, device_ip=ip,
                            rule=rule_label, old_ndgs=json.dumps(old_ndgs),
                            new_ndgs=json.dumps(new_ndgs),
                            targets=json.dumps(targets)))
        s.commit()


async def run_reconciliation() -> dict:
    if _running_lock.locked():
        return {"status": "skipped", "message": "reconciliation already running"}
    async with _running_lock:
        with SessionLocal() as s:
            run = ReconcileRun()
            s.add(run)
            s.commit()
            run_id = run.id
        stats = {"scanned": 0, "fixed": 0, "pending": 0, "unmatched": 0,
                 "not_found_in_cc": 0, "excluded": 0, "errors": 0}
        status, message = "done", ""
        approve = get_setting("reconcile.mode").strip().lower() == "approve"
        ttl = timedelta(hours=max(0, get_int("reconcile.detail_ttl_hours", 24)))
        excludes = _compile_excludes()
        first_error = ""
        cc: CatalystClient | None = None
        try:
            async with ISEClient() as ise:
                summaries = await ise.list_devices()
                now = datetime.now(timezone.utc)
                with SessionLocal() as s:
                    cache = {r.ise_id: (r.has_default, r.last_detail)
                             for r in s.query(ISEDeviceCache).all()}
                seen_ids = set()
                for summary in summaries:
                    stats["scanned"] += 1
                    dev_id, dev_name = summary.get("id", ""), summary.get("name", "")
                    if dev_id:
                        seen_ids.add(dev_id)
                    if _excluded(excludes, dev_name):
                        stats["excluded"] += 1
                        continue
                    cached = cache.get(dev_id)
                    if cached is not None:
                        has_default, last_detail = cached
                        if last_detail is not None and last_detail.tzinfo is None:
                            last_detail = last_detail.replace(tzinfo=timezone.utc)
                        if not has_default and last_detail and now - last_detail < ttl:
                            continue  # compliant recently — skip the detail GET
                    try:
                        full = await ise.get_device(dev_id)
                        if not full:
                            continue
                        ndgs = list(full.get("NetworkDeviceGroupList") or [])
                        ip = _first_ip(full)
                        _update_cache(dev_id, full.get("name", dev_name), ip, ndgs, now)
                        if not is_default_ndgs(ndgs):
                            continue
                        if _excluded(excludes, ip):
                            stats["excluded"] += 1
                            continue
                        if cc is None:
                            cc = CatalystClient()
                        device = await enrich_from_cc(
                            cc, {"hostname": full.get("name", ""), "ip": ip})
                        if device is None:
                            stats["not_found_in_cc"] += 1
                            continue
                        plan = plan_for_device(device)
                        if not plan["matched"]:
                            stats["unmatched"] += 1
                            continue
                        targets = plan["targets"]
                        if not targets.get("device_type") and not targets.get("location"):
                            stats["unmatched"] += 1
                            continue
                        new_ndgs = merge_ndgs(ndgs, targets.get("device_type"),
                                              targets.get("location"))
                        if sorted(new_ndgs) == sorted(ndgs):
                            continue  # rule matched but nothing would change
                        rule_label = f"#{plan['rule']['id']} {plan['rule']['description']}".strip()
                        if approve:
                            _queue_pending(dev_id, full.get("name", dev_name), ip,
                                           rule_label, ndgs, new_ndgs, targets)
                            stats["pending"] += 1
                        else:
                            old, new = await apply_to_ise(ise, full, targets)
                            _update_cache(dev_id, full.get("name", dev_name), ip, new, now)
                            audit.record("reconcile", "success",
                                         device_name=full.get("name", dev_name),
                                         device_ip=ip, rule=rule_label,
                                         old_ndgs=old, new_ndgs=new, message="updated")
                            stats["fixed"] += 1
                    except (ISEError, CatalystError) as exc:
                        stats["errors"] += 1
                        if not first_error:
                            first_error = f"{dev_name}: {exc}"
                        log.warning("reconcile: error on %s: %s", dev_name, exc)
                    except Exception as exc:  # noqa: BLE001 - keep scanning
                        stats["errors"] += 1
                        if not first_error:
                            first_error = f"{dev_name}: {exc}"
                        log.exception("reconcile: unexpected error on %s", dev_name)
                # forget devices that vanished from ISE
                if seen_ids:
                    with SessionLocal() as s:
                        s.query(ISEDeviceCache).filter(
                            ISEDeviceCache.ise_id.not_in(seen_ids)).delete(
                            synchronize_session=False)
                        s.commit()
        except Exception as exc:  # noqa: BLE001 - job must record any failure
            status, message = "failed", str(exc)
            log.exception("reconciliation run failed")
        finally:
            if cc is not None:
                await cc.aclose()
        if status == "done" and stats["errors"] and first_error:
            message = f"{stats['errors']} device(s) errored, first: {first_error}"
        with SessionLocal() as s:
            run = s.get(ReconcileRun, run_id)
            run.finished = datetime.now(timezone.utc)
            run.status = status
            run.message = message[:2000]
            for key in ("scanned", "fixed", "pending", "unmatched",
                        "not_found_in_cc", "excluded", "errors"):
                setattr(run, key, stats[key])
            s.commit()
        audit.record("reconcile", "info" if status == "done" else "failed",
                     message=f"reconciliation {status}: {stats} {message}".strip())
        return {"status": status, **stats, "message": message}


# --------------------------------------------------------------------------- pending changes
def list_pending() -> list[dict]:
    with SessionLocal() as s:
        rows = (s.query(PendingChange).filter(PendingChange.status == "pending")
                .order_by(PendingChange.id.desc()).all())
        return [{
            "id": r.id, "created": r.created.isoformat() if r.created else None,
            "device_name": r.device_name, "device_ip": r.device_ip, "rule": r.rule,
            "old_ndgs": json.loads(r.old_ndgs) if r.old_ndgs else [],
            "new_ndgs": json.loads(r.new_ndgs) if r.new_ndgs else [],
        } for r in rows]


def _resolve_pending(pid: int, status: str, message: str = ""):
    with SessionLocal() as s:
        row = s.get(PendingChange, pid)
        if row:
            row.status = status
            row.resolved = datetime.now(timezone.utc)
            row.message = message[:2000]
            s.commit()


async def apply_pending(pid: int) -> dict:
    """Approve one queued change: re-fetch the device and write the stored targets."""
    with SessionLocal() as s:
        row = s.get(PendingChange, pid)
        if row is None or row.status != "pending":
            return {"status": "failed", "message": "pending change not found"}
        ise_id, name, ip = row.ise_id, row.device_name, row.device_ip
        rule_label, targets = row.rule, json.loads(row.targets or "{}")
    try:
        async with ISEClient() as ise:
            full = await ise.get_device(ise_id)
            if not full:
                _resolve_pending(pid, "failed", "device no longer exists in ISE")
                return {"status": "failed", "message": "device no longer exists in ISE"}
            old, new = await apply_to_ise(ise, full, targets)
        _update_cache(ise_id, name, ip, new, datetime.now(timezone.utc))
        _resolve_pending(pid, "applied")
        audit.record("manual", "success", device_name=name, device_ip=ip,
                     rule=rule_label, old_ndgs=old, new_ndgs=new,
                     message="approved reconciliation change applied")
        return {"status": "success", "old_ndgs": old, "new_ndgs": new}
    except ISEError as exc:
        _resolve_pending(pid, "failed", str(exc))
        audit.record("manual", "failed", device_name=name, device_ip=ip,
                     rule=rule_label, message=f"approval failed: {exc}")
        return {"status": "failed", "message": str(exc)}


def reject_pending(pid: int) -> dict:
    _resolve_pending(pid, "rejected")
    return {"status": "rejected", "id": pid}


async def apply_all_pending() -> dict:
    results = {"applied": 0, "failed": 0}
    for item in list_pending():
        result = await apply_pending(item["id"])
        results["applied" if result["status"] == "success" else "failed"] += 1
    return results


def last_runs(limit: int = 10) -> list[dict]:
    with SessionLocal() as s:
        runs = (s.query(ReconcileRun).order_by(ReconcileRun.id.desc()).limit(limit).all())
        return [{
            "id": r.id,
            "started": r.started.isoformat() if r.started else None,
            "finished": r.finished.isoformat() if r.finished else None,
            "status": r.status, "scanned": r.scanned, "fixed": r.fixed,
            "pending": r.pending, "unmatched": r.unmatched,
            "not_found_in_cc": r.not_found_in_cc, "excluded": r.excluded,
            "errors": r.errors, "message": r.message,
        } for r in runs]


def is_running() -> bool:
    return _running_lock.locked()
