"""Reconciliation scope: 'defaults' skips non-default devices, 'all' enforces rules."""
import asyncio
import json
from unittest.mock import patch, AsyncMock

import app.settings_store as ss
from app.db import init_db, SessionLocal
from app.models import MappingRule, ISEDeviceCache
from app import reconcile

init_db()

ISE_DEVICE = {
    "id": "dev-142", "name": "SSTO142CIS.global.web-int.net",
    "NetworkDeviceIPList": [{"ipaddress": "10.7.0.142", "mask": 32}],
    # NOT on defaults, but different from what the rule prescribes:
    "NetworkDeviceGroupList": ["Location#All Locations#EMEA#STO",
                               "IPSEC#Is IPSEC Device#No",
                               "Device Type#All Device Types#Wired#Access Switchs#G4"],
}

CC_DEVICE = {"id": "cc-142", "hostname": "SSTO142CIS.global.web-int.net",
             "ip": "10.7.0.142", "family": "Switches and Hubs",
             "series": "Cisco Catalyst 3750", "platform": "WS-C3750X-48PF-S",
             "site": "Global/00_EMEA/Stockdorf - STO/Building 4", "tags": []}


def _seed_rule():
    with SessionLocal() as s:
        s.query(MappingRule).delete()
        s.add(MappingRule(priority=10, description="test_sto", enabled=True,
                          match_tag="", match_hostname="^SSTO1", match_family="",
                          match_series="", match_platform="", match_site="",
                          device_type_ndg="Device Type#All Device Types#Wired#Access Switchs",
                          location_mode="fixed",
                          location_ndg="Location#All Locations#EMEA#STO",
                          derive_fallback="skip", auto_create_location=False))
        s.commit()


def _run(scope: str):
    ss.set_setting("reconcile.scope", scope)
    ss.set_setting("reconcile.mode", "auto")
    ss.set_setting("ise.base_url", "https://ise.test.local")
    reconcile.invalidate_cache()
    updated = []

    ise = AsyncMock()
    ise.__aenter__ = AsyncMock(return_value=ise)
    ise.__aexit__ = AsyncMock(return_value=False)
    ise.list_devices = AsyncMock(return_value=[{"id": "dev-142",
                                                "name": ISE_DEVICE["name"]}])
    ise.get_device = AsyncMock(return_value=dict(ISE_DEVICE))

    async def fake_update(device):
        updated.append(device["NetworkDeviceGroupList"])
        return {}
    ise.update_device = fake_update
    ise.create_ndg = AsyncMock(return_value="exists")

    cc = AsyncMock()
    cc.aclose = AsyncMock()

    with patch.object(reconcile, "ISEClient", return_value=ise), \
         patch.object(reconcile, "CatalystClient", return_value=cc), \
         patch.object(reconcile, "enrich_from_cc",
                      AsyncMock(return_value=dict(CC_DEVICE))):
        result = asyncio.run(reconcile.run_reconciliation())
    return result, updated


def test_defaults_scope_skips_non_default_device():
    _seed_rule()
    result, updated = _run("defaults")
    assert result["status"] == "done"
    assert result["fixed"] == 0
    assert updated == []  # device not on defaults -> untouched


def test_all_scope_enforces_rule_on_non_default_device():
    _seed_rule()
    result, updated = _run("all")
    assert result["status"] == "done"
    assert result["fixed"] == 1
    assert len(updated) == 1
    new = updated[0]
    assert "Device Type#All Device Types#Wired#Access Switchs" in new
    assert "Location#All Locations#EMEA#STO" in new
    assert "IPSEC#Is IPSEC Device#No" in new  # other dimensions preserved
    assert "Device Type#All Device Types#Wired#Access Switchs#G4" not in new


def test_all_scope_second_run_uses_compliance_cache():
    _seed_rule()
    _run("all")  # fixes the device, marks it compliant
    # second run without invalidation: device is compliant + fresh -> no detail fetch
    ss.set_setting("reconcile.scope", "all")
    ise = AsyncMock()
    ise.__aenter__ = AsyncMock(return_value=ise)
    ise.__aexit__ = AsyncMock(return_value=False)
    ise.list_devices = AsyncMock(return_value=[{"id": "dev-142",
                                                "name": ISE_DEVICE["name"]}])
    ise.get_device = AsyncMock()
    with patch.object(reconcile, "ISEClient", return_value=ise), \
         patch.object(reconcile, "CatalystClient", AsyncMock()):
        result = asyncio.run(reconcile.run_reconciliation())
    assert result["status"] == "done"
    ise.get_device.assert_not_called()


def test_invalidate_cache_clears_compliance():
    with SessionLocal() as s:
        s.merge(ISEDeviceCache(ise_id="inv-1", name="X", compliant=True,
                               ndgs=json.dumps([])))
        s.commit()
    reconcile.invalidate_cache()
    with SessionLocal() as s:
        row = s.get(ISEDeviceCache, "inv-1")
        assert row.compliant is False
        assert row.last_detail is None
