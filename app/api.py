"""HTTP API: GUI JSON endpoints, webhook listener, healthcheck."""
import asyncio
import csv
import io
import json
import logging
import secrets
import time

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from . import audit, ndg, reconcile
from .clients.catalyst import CatalystClient, CatalystError
from .clients.ise import ISEClient, ISEError
from .db import SessionLocal
from .models import MappingRule, SiteMapping, AuditLog
from .settings_store import (all_settings_masked, save_settings, get_setting)
from .sync import dry_run
from .webhook import queue, extract_device_ref
from . import scheduler as sched

log = logging.getLogger(__name__)
router = APIRouter()

# --------------------------------------------------------------------------- health
_health_cache = {"ts": 0.0, "data": None}


async def _check(coro) -> bool:
    try:
        await asyncio.wait_for(coro, timeout=8)
        return True
    except Exception:  # noqa: BLE001 - any failure means "unreachable"
        return False


@router.get("/healthz")
async def healthz():
    now = time.monotonic()
    if _health_cache["data"] is None or now - _health_cache["ts"] > 60:
        cc_ok = ise_ok = False
        if get_setting("cc.base_url"):
            try:
                async with CatalystClient() as cc:
                    cc_ok = await _check(cc._get_token())
            except CatalystError:
                cc_ok = False
        if get_setting("ise.base_url"):
            try:
                async with ISEClient() as ise:
                    ise_ok = await _check(ise.list_ndgs())
            except ISEError:
                ise_ok = False
        _health_cache.update(ts=now, data={"status": "ok", "cc_reachable": cc_ok,
                                           "ise_reachable": ise_ok})
    return _health_cache["data"]


# --------------------------------------------------------------------------- webhook
@router.post("/webhook/{rest:path}")
async def webhook(rest: str, request: Request):
    configured = get_setting("webhook.path") or "/webhook/catalystcenter"
    if request.url.path.rstrip("/") != configured.rstrip("/"):
        raise HTTPException(404, "unknown webhook path")
    token = get_setting("webhook.token")
    if token:
        sent = (request.headers.get("X-Auth-Token")
                or request.headers.get("Authorization", "").removeprefix("Bearer ").strip())
        if not sent or not secrets.compare_digest(sent, token):
            audit.record("webhook", "failed", message="webhook rejected: bad or missing token")
            raise HTTPException(401, "invalid token")
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - CC may send odd bodies; keep the raw text
        payload = {"_raw_body": (await request.body())[:10000].decode(errors="replace")}
    ref = extract_device_ref(payload)
    if not ref:
        audit.record("webhook", "skipped",
                     message="no device reference found in payload", raw=payload)
        return {"status": "ignored", "reason": "no device reference found"}
    audit.record("webhook", "info", device_name=ref.get("hostname", ""),
                 device_ip=ref.get("ip", ""),
                 message=f"webhook received, queued (ref={ref})", raw=payload)
    await queue.submit(ref, raw=payload)
    return {"status": "queued", "device": ref}


@router.post("/api/webhook-test")
async def webhook_test(request: Request):
    """Replay a sample payload through parsing + the rule engine (dry-run)."""
    payload = await request.json()
    ref = extract_device_ref(payload)
    if not ref:
        return {"status": "failed", "message": "no device reference found in payload",
                "parsed_ref": None}
    try:
        result = await dry_run(ref)
    except (CatalystError, ISEError) as exc:
        return {"status": "failed", "parsed_ref": ref, "message": str(exc)}
    return {"parsed_ref": ref, **result}


# --------------------------------------------------------------------------- settings
@router.get("/api/settings")
async def get_settings():
    return all_settings_masked()


@router.put("/api/settings")
async def put_settings(payload: dict):
    changed = save_settings(payload)
    if any(k.startswith(("ndg.", "reconcile.")) for k in changed):
        sched.configure_jobs()
    return {"changed": changed}


@router.post("/api/test/cc")
async def test_cc():
    try:
        async with CatalystClient() as cc:
            return await cc.test_connection()
    except Exception as exc:  # noqa: BLE001 - report any failure to GUI
        return {"ok": False, "message": str(exc)}


@router.post("/api/test/ise")
async def test_ise():
    try:
        async with ISEClient() as ise:
            return await ise.test_connection()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": str(exc),
                "hint": "Enable ERS under Administration > System > Settings > API Settings "
                        "and give the API user the 'ERS Admin' role."}


# --------------------------------------------------------------------------- NDGs
@router.get("/api/ndg")
async def list_ndg(type: str | None = None):
    return ndg.get_cached_ndgs(type)


@router.post("/api/ndg/refresh")
async def refresh_ndg():
    try:
        return await ndg.refresh_ndg_cache()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"NDG refresh failed: {exc}")


# --------------------------------------------------------------------------- rules
RULE_FIELDS = ("priority", "description", "enabled", "match_tag", "match_hostname",
               "match_family", "match_series", "match_platform", "match_site",
               "device_type_ndg", "location_mode", "location_ndg", "derive_fallback",
               "auto_create_location")


def _rule_dict(r: MappingRule) -> dict:
    return {"id": r.id, **{f: getattr(r, f) for f in RULE_FIELDS}}


@router.get("/api/rules")
async def list_rules():
    with SessionLocal() as s:
        rules = s.query(MappingRule).order_by(MappingRule.priority, MappingRule.id).all()
        return [_rule_dict(r) for r in rules]


@router.post("/api/rules")
async def create_rule(payload: dict):
    with SessionLocal() as s:
        rule = MappingRule(**{f: payload.get(f, MappingRule.__table__.c[f].default.arg)
                              for f in RULE_FIELDS})
        s.add(rule)
        s.commit()
        return _rule_dict(rule)


@router.put("/api/rules/{rule_id}")
async def update_rule(rule_id: int, payload: dict):
    with SessionLocal() as s:
        rule = s.get(MappingRule, rule_id)
        if rule is None:
            raise HTTPException(404, "rule not found")
        for f in RULE_FIELDS:
            if f in payload:
                setattr(rule, f, payload[f])
        s.commit()
        return _rule_dict(rule)


@router.delete("/api/rules/{rule_id}")
async def delete_rule(rule_id: int):
    with SessionLocal() as s:
        rule = s.get(MappingRule, rule_id)
        if rule is None:
            raise HTTPException(404, "rule not found")
        s.delete(rule)
        s.commit()
    return {"deleted": rule_id}


@router.post("/api/rules/reorder")
async def reorder_rules(payload: dict):
    """payload: {"order": [rule_id, ...]} — priorities re-assigned 10,20,30..."""
    order = payload.get("order") or []
    with SessionLocal() as s:
        for idx, rule_id in enumerate(order):
            rule = s.get(MappingRule, int(rule_id))
            if rule:
                rule.priority = (idx + 1) * 10
        s.commit()
    return {"ok": True}


# --------------------------------------------------------------------------- site mappings
@router.get("/api/sitemap")
async def list_sitemap():
    with SessionLocal() as s:
        rows = s.query(SiteMapping).order_by(SiteMapping.priority, SiteMapping.id).all()
        return [{"id": r.id, "priority": r.priority, "site_pattern": r.site_pattern,
                 "location_ndg": r.location_ndg} for r in rows]


@router.post("/api/sitemap")
async def create_sitemap(payload: dict):
    if not payload.get("site_pattern") or not payload.get("location_ndg"):
        raise HTTPException(400, "site_pattern and location_ndg are required")
    with SessionLocal() as s:
        row = SiteMapping(priority=int(payload.get("priority", 100)),
                          site_pattern=payload["site_pattern"],
                          location_ndg=payload["location_ndg"])
        s.add(row)
        s.commit()
        return {"id": row.id}


@router.put("/api/sitemap/{map_id}")
async def update_sitemap(map_id: int, payload: dict):
    with SessionLocal() as s:
        row = s.get(SiteMapping, map_id)
        if row is None:
            raise HTTPException(404, "mapping not found")
        for f in ("priority", "site_pattern", "location_ndg"):
            if f in payload:
                setattr(row, f, payload[f])
        s.commit()
    return {"ok": True}


@router.delete("/api/sitemap/{map_id}")
async def delete_sitemap(map_id: int):
    with SessionLocal() as s:
        row = s.get(SiteMapping, map_id)
        if row is None:
            raise HTTPException(404, "mapping not found")
        s.delete(row)
        s.commit()
    return {"deleted": map_id}


# --------------------------------------------------------------------------- dry-run / CC inventory
@router.get("/api/cc/devices")
async def cc_devices():
    try:
        async with CatalystClient() as cc:
            return await cc.list_devices()
    except CatalystError as exc:
        raise HTTPException(502, str(exc))


@router.post("/api/dryrun")
async def dryrun(payload: dict):
    ref = {k: payload.get(k, "") for k in ("id", "hostname", "ip")}
    if not any(ref.values()):
        raise HTTPException(400, "provide id, hostname or ip")
    try:
        return await dry_run(ref)
    except (CatalystError, ISEError) as exc:
        return {"status": "failed", "message": str(exc)}


# --------------------------------------------------------------------------- audit
@router.get("/api/audit")
async def get_audit(limit: int = 100, offset: int = 0, status: str | None = None,
                    trigger: str | None = None):
    limit = min(max(limit, 1), 500)
    with SessionLocal() as s:
        q = s.query(AuditLog)
        if status:
            q = q.filter(AuditLog.status == status)
        if trigger:
            q = q.filter(AuditLog.trigger == trigger)
        total = q.count()
        rows = q.order_by(AuditLog.id.desc()).offset(offset).limit(limit).all()
        return {"total": total, "items": [{
            "id": r.id, "ts": r.ts.isoformat() if r.ts else None, "trigger": r.trigger,
            "device_name": r.device_name, "device_ip": r.device_ip, "rule": r.rule,
            "old_ndgs": json.loads(r.old_ndgs) if r.old_ndgs else [],
            "new_ndgs": json.loads(r.new_ndgs) if r.new_ndgs else [],
            "status": r.status, "message": r.message, "raw": r.raw,
        } for r in rows]}


@router.get("/api/audit.csv")
async def audit_csv():
    def generate():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["id", "timestamp", "trigger", "device_name", "device_ip",
                         "rule", "old_ndgs", "new_ndgs", "status", "message"])
        with SessionLocal() as s:
            for r in s.query(AuditLog).order_by(AuditLog.id).yield_per(200):
                writer.writerow([r.id, r.ts.isoformat() if r.ts else "", r.trigger,
                                 r.device_name, r.device_ip, r.rule, r.old_ndgs,
                                 r.new_ndgs, r.status, r.message])
                if buf.tell() > 64 * 1024:
                    yield buf.getvalue()
                    buf.seek(0)
                    buf.truncate()
        yield buf.getvalue()

    return StreamingResponse(generate(), media_type="text/csv", headers={
        "Content-Disposition": "attachment; filename=audit_log.csv"})


# --------------------------------------------------------------------------- dashboard / reconcile
@router.get("/api/dashboard")
async def dashboard():
    counts = audit.dashboard_counts()
    health = await healthz()
    runs = reconcile.last_runs(1)
    return {**counts, "cc_reachable": health["cc_reachable"],
            "ise_reachable": health["ise_reachable"],
            "pending_webhooks": queue.pending(),
            "last_reconcile": runs[0] if runs else None}


@router.get("/api/reconcile")
async def reconcile_status():
    return {"running": reconcile.is_running(), "runs": reconcile.last_runs(10),
            "pending": reconcile.list_pending()}


@router.post("/api/reconcile/run")
async def reconcile_run():
    if reconcile.is_running():
        return {"status": "skipped", "message": "already running"}
    asyncio.create_task(reconcile.run_reconciliation())
    return {"status": "started"}


# --------------------------------------------------------------------------- pending approvals
@router.get("/api/pending")
async def pending_list():
    return reconcile.list_pending()


@router.post("/api/pending/{pid}/approve")
async def pending_approve(pid: int):
    return await reconcile.apply_pending(pid)


@router.post("/api/pending/{pid}/reject")
async def pending_reject(pid: int):
    return reconcile.reject_pending(pid)


@router.post("/api/pending/approve-all")
async def pending_approve_all():
    return await reconcile.apply_all_pending()


# --------------------------------------------------------------------------- export / import
@router.get("/api/export")
async def export_config():
    with SessionLocal() as s:
        rules = s.query(MappingRule).order_by(MappingRule.priority).all()
        maps = s.query(SiteMapping).order_by(SiteMapping.priority).all()
    settings = {k: v["value"] for k, v in all_settings_masked().items() if not v["secret"]}
    data = {
        "version": 1,
        "settings": settings,
        "rules": [{f: getattr(r, f) for f in RULE_FIELDS} for r in rules],
        "site_mappings": [{"priority": m.priority, "site_pattern": m.site_pattern,
                           "location_ndg": m.location_ndg} for m in maps],
    }
    return Response(json.dumps(data, indent=2), media_type="application/json",
                    headers={"Content-Disposition": "attachment; filename=ise-ndg-sync-config.json"})


@router.post("/api/import")
async def import_config(payload: dict):
    imported = {"rules": 0, "site_mappings": 0, "settings": 0}
    with SessionLocal() as s:
        if payload.get("rules"):
            s.query(MappingRule).delete()
            for r in payload["rules"]:
                s.add(MappingRule(**{f: r.get(f) for f in RULE_FIELDS if f in r}))
                imported["rules"] += 1
        if payload.get("site_mappings"):
            s.query(SiteMapping).delete()
            for m in payload["site_mappings"]:
                s.add(SiteMapping(priority=int(m.get("priority", 100)),
                                  site_pattern=m.get("site_pattern", ""),
                                  location_ndg=m.get("location_ndg", "")))
                imported["site_mappings"] += 1
        s.commit()
    if payload.get("settings"):
        imported["settings"] = len(save_settings(payload["settings"]))
        sched.configure_jobs()
    audit.record("manual", "info", message=f"configuration imported: {imported}")
    return imported
