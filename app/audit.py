"""Audit-log helpers."""
import json
import logging
from datetime import datetime, timedelta, timezone

from .db import SessionLocal
from .models import AuditLog

log = logging.getLogger(__name__)


def record(trigger: str, status: str, device_name: str = "", device_ip: str = "",
           rule: str = "", old_ndgs: list | None = None, new_ndgs: list | None = None,
           message: str = "", raw=None) -> int:
    with SessionLocal() as s:
        entry = AuditLog(
            trigger=trigger, status=status, device_name=device_name, device_ip=device_ip,
            rule=rule,
            old_ndgs=json.dumps(old_ndgs) if old_ndgs is not None else "",
            new_ndgs=json.dumps(new_ndgs) if new_ndgs is not None else "",
            message=message[:4000],
            raw=json.dumps(raw, default=str)[:20000] if raw is not None else "",
        )
        s.add(entry)
        s.commit()
        log.info("audit[%s/%s] %s %s", trigger, status, device_name, message)
        return entry.id


def update_status(entry_id: int, status: str, message: str = "", new_ndgs: list | None = None):
    with SessionLocal() as s:
        entry = s.get(AuditLog, entry_id)
        if entry:
            entry.status = status
            if message:
                entry.message = message[:4000]
            if new_ndgs is not None:
                entry.new_ndgs = json.dumps(new_ndgs)
            s.commit()


def dashboard_counts() -> dict:
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    with SessionLocal() as s:
        q = s.query(AuditLog).filter(AuditLog.ts >= since)
        events = q.filter(AuditLog.trigger == "webhook").count()
        updated = q.filter(AuditLog.status == "success").count()
        failures = q.filter(AuditLog.status == "failed").count()
    return {"webhook_events_24h": events, "devices_updated_24h": updated,
            "failures_24h": failures}
