"""Reconciliation job: scan ISE for devices still on default NDGs and fix them."""
import asyncio
import logging
from datetime import datetime, timezone

from .clients.ise import ISEClient, ISEError
from .db import SessionLocal
from .models import ReconcileRun
from .rules import is_default_ndgs
from .sync import process_device_event
from . import audit

log = logging.getLogger(__name__)

_running_lock = asyncio.Lock()


async def run_reconciliation() -> dict:
    if _running_lock.locked():
        return {"status": "skipped", "message": "reconciliation already running"}
    async with _running_lock:
        with SessionLocal() as s:
            run = ReconcileRun()
            s.add(run)
            s.commit()
            run_id = run.id
        stats = {"scanned": 0, "fixed": 0, "unmatched": 0, "not_found_in_cc": 0, "errors": 0}
        status, message = "done", ""
        try:
            ise = ISEClient()
            devices = await ise.list_devices()
            for summary in devices:
                stats["scanned"] += 1
                try:
                    full = await ise.get_device(summary["id"])
                    if not full:
                        continue
                    ndgs = list(full.get("NetworkDeviceGroupList") or [])
                    if not is_default_ndgs(ndgs):
                        continue
                    result = await process_device_event(
                        {"hostname": full.get("name", ""),
                         "ip": _first_ip(full)},
                        trigger="reconcile", ise_device=full)
                    if result["status"] == "success":
                        stats["fixed"] += 1
                    elif "not found in Catalyst Center" in result.get("message", ""):
                        stats["not_found_in_cc"] += 1
                    elif result["status"] == "skipped":
                        stats["unmatched"] += 1
                    else:
                        stats["errors"] += 1
                except ISEError as exc:
                    stats["errors"] += 1
                    log.warning("reconcile: error on %s: %s", summary.get("name"), exc)
        except Exception as exc:  # noqa: BLE001 - job must record any failure
            status, message = "failed", str(exc)
            log.exception("reconciliation run failed")
        with SessionLocal() as s:
            run = s.get(ReconcileRun, run_id)
            run.finished = datetime.now(timezone.utc)
            run.status = status
            run.message = message
            run.scanned = stats["scanned"]
            run.fixed = stats["fixed"]
            run.unmatched = stats["unmatched"]
            run.not_found_in_cc = stats["not_found_in_cc"]
            run.errors = stats["errors"]
            s.commit()
        audit.record("reconcile", "info" if status == "done" else "failed",
                     message=f"reconciliation {status}: {stats} {message}".strip())
        return {"status": status, **stats, "message": message}


def _first_ip(ise_device: dict) -> str:
    ips = ise_device.get("NetworkDeviceIPList") or []
    return (ips[0] or {}).get("ipaddress", "") if ips else ""


def last_runs(limit: int = 10) -> list[dict]:
    with SessionLocal() as s:
        runs = (s.query(ReconcileRun).order_by(ReconcileRun.id.desc()).limit(limit).all())
        return [{
            "id": r.id,
            "started": r.started.isoformat() if r.started else None,
            "finished": r.finished.isoformat() if r.finished else None,
            "status": r.status, "scanned": r.scanned, "fixed": r.fixed,
            "unmatched": r.unmatched, "not_found_in_cc": r.not_found_in_cc,
            "errors": r.errors, "message": r.message,
        } for r in runs]


def is_running() -> bool:
    return _running_lock.locked()
