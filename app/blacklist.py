"""Per-device blacklist: devices marked here are never modified, by neither the
webhook pipeline nor reconciliation.

Matching is case-insensitive. A name entry matches both the FQDN and the short
hostname in either direction (ISE and CC often disagree); an IP entry matches
exactly. An entry with both fields set matches on either.
"""
import logging

from .db import SessionLocal
from .models import BlacklistEntry

log = logging.getLogger(__name__)


def _norm(value: str) -> str:
    return (value or "").strip().lower()


def _short(value: str) -> str:
    return _norm(value).split(".")[0]


def load() -> list[dict]:
    with SessionLocal() as s:
        return [{"id": r.id, "created": r.created.isoformat() if r.created else None,
                 "name": r.name, "ip": r.ip, "note": r.note}
                for r in s.query(BlacklistEntry).order_by(BlacklistEntry.id.desc()).all()]


def matches(entries: list[dict], name: str = "", ip: str = "") -> bool:
    """Check a device against pre-loaded entries (use in loops)."""
    n, sn, i = _norm(name), _short(name), _norm(ip)
    for e in entries:
        en, ei = _norm(e.get("name", "")), _norm(e.get("ip", ""))
        if en and sn and (en == n or _short(en) == sn):
            return True
        if ei and i and ei == i:
            return True
    return False


def is_blacklisted(name: str = "", ip: str = "") -> bool:
    """Convenience single-device check (webhook path)."""
    if not name and not ip:
        return False
    return matches(load(), name, ip)


def add(name: str = "", ip: str = "", note: str = "") -> dict:
    name, ip = name.strip(), ip.strip()
    if not name and not ip:
        raise ValueError("name or ip is required")
    with SessionLocal() as s:
        dup = (s.query(BlacklistEntry)
               .filter(BlacklistEntry.name == name, BlacklistEntry.ip == ip).first())
        if dup:
            return {"id": dup.id, "duplicate": True}
        row = BlacklistEntry(name=name, ip=ip, note=note.strip())
        s.add(row)
        s.commit()
        log.info("blacklisted device name=%r ip=%r (%s)", name, ip, note)
        return {"id": row.id, "duplicate": False}


def remove(entry_id: int) -> bool:
    with SessionLocal() as s:
        row = s.get(BlacklistEntry, entry_id)
        if row is None:
            return False
        s.delete(row)
        s.commit()
        return True
