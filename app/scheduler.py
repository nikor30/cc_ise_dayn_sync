"""APScheduler jobs: periodic NDG cache refresh + reconciliation."""
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .settings_store import get_bool, get_int
from .ndg import refresh_ndg_cache
from .reconcile import run_reconciliation

log = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()


async def _ndg_job():
    try:
        await refresh_ndg_cache()
    except Exception as exc:  # noqa: BLE001 - scheduled job must not die
        log.warning("scheduled NDG refresh failed: %s", exc)


async def _reconcile_job():
    if not get_bool("reconcile.enabled"):
        return
    try:
        await run_reconciliation()
    except Exception as exc:  # noqa: BLE001
        log.warning("scheduled reconciliation failed: %s", exc)


def configure_jobs():
    """(Re)apply intervals from settings. Called at startup and on settings save."""
    hours = max(1, get_int("ndg.refresh_hours", 6))
    minutes = max(5, get_int("reconcile.minutes", 30))
    scheduler.add_job(_ndg_job, "interval", hours=hours, id="ndg_refresh",
                      replace_existing=True, max_instances=1)
    scheduler.add_job(_reconcile_job, "interval", minutes=minutes, id="reconcile",
                      replace_existing=True, max_instances=1)
    log.info("scheduler: NDG refresh every %dh, reconciliation every %dmin (enabled=%s)",
             hours, minutes, get_bool("reconcile.enabled"))


def start():
    configure_jobs()
    if not scheduler.running:
        scheduler.start()
