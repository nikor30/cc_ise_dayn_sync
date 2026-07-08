"""Webhook listener: defensive payload parsing + per-device debounce queue.

Catalyst Center payload formats vary by event type, so we hunt for a device
reference (networkDeviceId / hostname / managementIpAddress) across the common
shapes and log the full raw payload to the audit log.
"""
import asyncio
import logging

from . import audit
from .settings_store import get_int
from .sync import process_device_event

log = logging.getLogger(__name__)

ID_KEYS = ("networkDeviceId", "deviceId", "deviceUuid", "instanceUuid", "id")
NAME_KEYS = ("hostname", "hostName", "deviceName", "name", "device_name", "managementIpAddr")
IP_KEYS = ("managementIpAddress", "managementIpAddr", "ipAddress", "deviceIp", "ip")


def _walk(obj, out: dict, depth: int = 0):
    if depth > 6 or not isinstance(obj, (dict, list)):
        return
    if isinstance(obj, list):
        for item in obj:
            _walk(item, out, depth + 1)
        return
    for key, val in obj.items():
        if isinstance(val, (dict, list)):
            _walk(val, out, depth + 1)
        elif isinstance(val, str) and val:
            if key in ID_KEYS and not out.get("id") and len(val) >= 8 and " " not in val:
                out["id"] = val
            elif key in IP_KEYS and not out.get("ip") and val.count(".") == 3:
                out["ip"] = val
            elif key in NAME_KEYS and not out.get("hostname"):
                if val.count(".") == 3 and val.replace(".", "").isdigit():
                    out.setdefault("ip", val)
                else:
                    out["hostname"] = val


def extract_device_ref(payload) -> dict:
    """Best-effort extraction of {id, hostname, ip} from any CC event payload."""
    out: dict = {}
    if isinstance(payload, (dict, list)):
        _walk(payload, out)
    return out


class DebounceQueue:
    """Coalesce bursts of events per device: (re)start a timer on each event,
    process once the configured quiet period has elapsed."""

    def __init__(self):
        self._timers: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _key(ref: dict) -> str:
        return ref.get("id") or ref.get("hostname") or ref.get("ip") or "?"

    async def submit(self, ref: dict, raw=None):
        key = self._key(ref)
        delay = get_int("sync.debounce_seconds", 60)
        async with self._lock:
            old = self._timers.pop(key, None)
            if old and not old.done():
                old.cancel()
            self._timers[key] = asyncio.create_task(self._fire(key, ref, raw, delay))
        log.info("webhook queued device %s (debounce %ds)", key, delay)

    async def _fire(self, key: str, ref: dict, raw, delay: int):
        try:
            await asyncio.sleep(delay)
            await process_device_event(ref, trigger="webhook", raw=raw)
        except asyncio.CancelledError:
            pass  # superseded by a newer event for the same device
        except Exception:
            log.exception("webhook processing failed for %s", key)
            audit.record("webhook", "failed", device_name=key,
                         message="unhandled error during processing", raw=raw)
        finally:
            async with self._lock:
                if self._timers.get(key) is asyncio.current_task():
                    del self._timers[key]

    def pending(self) -> int:
        return len(self._timers)


queue = DebounceQueue()


class DebouncedAction:
    """Run one async action after a quiet period, coalescing repeated triggers
    (used for 'webhook received -> run reconciliation')."""

    def __init__(self, action, name: str = ""):
        self._action = action
        self._task: asyncio.Task | None = None
        self.name = name

    def schedule(self, delay: int):
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = asyncio.create_task(self._fire(delay))
        log.info("scheduled %s in %ds", self.name, delay)

    async def _fire(self, delay: int):
        try:
            await asyncio.sleep(delay)
            await self._action()
        except asyncio.CancelledError:
            pass  # superseded by a newer trigger
        except Exception:  # noqa: BLE001 - background action must not crash the app
            log.exception("debounced action %s failed", self.name)


async def _run_reconciliation():
    from .reconcile import run_reconciliation  # avoid import cycle
    await run_reconciliation()


reconcile_trigger = DebouncedAction(_run_reconciliation, "webhook-triggered reconciliation")
