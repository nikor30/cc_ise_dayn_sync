"""Reconciliation helpers: exclusions, device cache, pending-approval flow."""
import asyncio
import json
from datetime import datetime, timezone

from app.db import init_db, SessionLocal
from app.models import PendingChange, ISEDeviceCache
from app import reconcile

init_db()


def test_exclude_patterns_compile_and_match():
    import app.settings_store as ss
    ss.set_setting("reconcile.exclude", "^lab-.*\n10\\.99\\.\n[broken")
    pats = reconcile._compile_excludes()
    assert len(pats) == 2  # invalid pattern dropped
    assert reconcile._excluded(pats, "LAB-SW01")            # case-insensitive
    assert reconcile._excluded(pats, "core-1", "10.99.1.2")  # matches IP
    assert not reconcile._excluded(pats, "SW-SCH-A01", "10.10.10.10")
    ss.set_setting("reconcile.exclude", "")


def test_exclude_supports_comma_separation():
    import app.settings_store as ss
    ss.set_setting("reconcile.exclude", "^lab-, ^test-")
    pats = reconcile._compile_excludes()
    assert reconcile._excluded(pats, "test-sw")
    assert reconcile._excluded(pats, "lab-sw")
    ss.set_setting("reconcile.exclude", "")


def test_update_cache_flags_default_ndgs():
    now = datetime.now(timezone.utc)
    reconcile._update_cache("id-1", "SW-1", "10.0.0.1",
                            ["Device Type#All Device Types", "Location#All Locations#DE"], now)
    reconcile._update_cache("id-2", "SW-2", "10.0.0.2",
                            ["Device Type#All Device Types#Wired",
                             "Location#All Locations#DE"], now)
    with SessionLocal() as s:
        assert s.get(ISEDeviceCache, "id-1").has_default is True
        assert s.get(ISEDeviceCache, "id-2").has_default is False


def test_queue_pending_supersedes_older_entry():
    reconcile._queue_pending("ise-1", "SW-1", "10.0.0.1", "#1 test",
                             ["Device Type#All Device Types"],
                             ["Device Type#All Device Types#Wired"],
                             {"device_type": "Device Type#All Device Types#Wired"})
    reconcile._queue_pending("ise-1", "SW-1", "10.0.0.1", "#1 test",
                             ["Device Type#All Device Types"],
                             ["Device Type#All Device Types#Wired#Access"],
                             {"device_type": "Device Type#All Device Types#Wired#Access"})
    pending = reconcile.list_pending()
    mine = [p for p in pending if p["device_name"] == "SW-1"]
    assert len(mine) == 1
    assert mine[0]["new_ndgs"] == ["Device Type#All Device Types#Wired#Access"]


def test_reject_pending():
    reconcile._queue_pending("ise-2", "SW-2", "10.0.0.2", "#1 test",
                             ["Location#All Locations"], ["Location#All Locations#DE"],
                             {"location": "Location#All Locations#DE"})
    pid = [p for p in reconcile.list_pending() if p["device_name"] == "SW-2"][0]["id"]
    assert reconcile.reject_pending(pid)["status"] == "rejected"
    assert not [p for p in reconcile.list_pending() if p["id"] == pid]
    with SessionLocal() as s:
        assert s.get(PendingChange, pid).status == "rejected"


def test_apply_pending_missing_row():
    result = asyncio.run(reconcile.apply_pending(999999))
    assert result["status"] == "failed"


def test_pending_targets_json_roundtrip():
    targets = {"device_type": "Device Type#All Device Types#Wired",
               "location": "Location#All Locations#APAC#WUH", "create_location": False}
    reconcile._queue_pending("ise-3", "SW-3", "", "#2 wuh", [], [], targets)
    with SessionLocal() as s:
        row = (s.query(PendingChange).filter(PendingChange.ise_id == "ise-3",
                                             PendingChange.status == "pending").one())
        assert json.loads(row.targets) == targets
