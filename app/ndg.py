"""NDG cache: periodic + on-demand refresh of Network Device Groups from ISE."""
import logging
from datetime import datetime, timezone

from .clients.ise import ISEClient
from .db import SessionLocal
from .models import NDGCache
from . import audit

log = logging.getLogger(__name__)


def classify(name: str) -> str:
    if name.startswith("Device Type#"):
        return "device_type"
    if name.startswith("Location#"):
        return "location"
    return "other"


async def refresh_ndg_cache() -> dict:
    """Pull all NDGs from ISE into the ndg_cache table. Returns counts."""
    async with ISEClient() as ise:
        ndgs = await ise.list_ndgs()
    now = datetime.now(timezone.utc)
    with SessionLocal() as s:
        seen = set()
        for item in ndgs:
            name = item["name"]
            if not name or name in seen:
                continue
            seen.add(name)
            row = s.query(NDGCache).filter(NDGCache.name == name).one_or_none()
            if row is None:
                row = NDGCache(name=name, ndg_type=classify(name), ise_id=item.get("id", ""))
                s.add(row)
            else:
                row.ise_id = item.get("id", "") or row.ise_id
                row.ndg_type = classify(name)
            row.last_seen = now
        # drop entries that vanished from ISE
        removed = s.query(NDGCache).filter(NDGCache.last_seen < now).delete()
        s.commit()
    counts = {
        "total": len(seen),
        "device_types": sum(1 for n in seen if classify(n) == "device_type"),
        "locations": sum(1 for n in seen if classify(n) == "location"),
        "removed": removed,
    }
    audit.record("system", "info", message=f"NDG cache refreshed: {counts}")
    log.info("NDG cache refreshed: %s", counts)
    return counts


def get_cached_ndgs(ndg_type: str | None = None) -> list[dict]:
    with SessionLocal() as s:
        q = s.query(NDGCache)
        if ndg_type:
            q = q.filter(NDGCache.ndg_type == ndg_type)
        return [{"name": r.name, "type": r.ndg_type, "ise_id": r.ise_id,
                 "last_seen": r.last_seen.isoformat() if r.last_seen else None}
                for r in q.order_by(NDGCache.name).all()]
