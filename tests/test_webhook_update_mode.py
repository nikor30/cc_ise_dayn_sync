"""webhook.update_mode: off / defaults-only / overwrite behavior."""
import asyncio
from unittest.mock import patch, AsyncMock

import app.settings_store as ss
from app.db import init_db
from app import sync

init_db()

CC_DEVICE = {"id": "cc-146", "hostname": "SSTO146CIS.Global.web-int.net",
             "ip": "172.20.10.146", "family": "Switches and Hubs", "series": "X",
             "platform": "WS-C3850", "site": "Global/00_EMEA/Stockdorf - STO",
             "tags": []}


def _ise_device(ndgs):
    return {"id": "ise-146", "name": "SSTO146CIS",
            "NetworkDeviceIPList": [{"ipaddress": "10.7.0.146", "mask": 32}],
            "NetworkDeviceGroupList": ndgs}


def _run(mode, current_ndgs):
    ss.set_setting("webhook.update_mode", mode)
    ss.set_setting("ise.base_url", "https://ise.test.local")
    updated = []

    plan = {"matched": True, "device": CC_DEVICE,
            "rule": {"id": 1, "description": "t", "priority": 10},
            "targets": {"device_type": "Device Type#All Device Types#Wired",
                        "location": "Location#All Locations#EMEA#STO",
                        "create_location": False, "notes": []}}

    ise = AsyncMock()
    ise.__aenter__ = AsyncMock(return_value=ise)
    ise.__aexit__ = AsyncMock(return_value=False)
    ise.find_device = AsyncMock(return_value=_ise_device(current_ndgs))

    async def fake_update(device):
        updated.append(device["NetworkDeviceGroupList"])
        return {}
    ise.update_device = fake_update

    cc = AsyncMock()
    cc.__aenter__ = AsyncMock(return_value=cc)
    cc.__aexit__ = AsyncMock(return_value=False)

    with patch.object(sync, "CatalystClient", return_value=cc), \
         patch.object(sync, "ISEClient", return_value=ise), \
         patch.object(sync, "enrich_from_cc", AsyncMock(return_value=dict(CC_DEVICE))), \
         patch.object(sync, "plan_for_device", return_value=plan):
        result = asyncio.run(sync.process_device_event(
            {"hostname": "SSTO146CIS.Global.web-int.net", "ip": "172.20.10.146"},
            trigger="webhook"))
    ss.set_setting("webhook.update_mode", "overwrite")
    return result, updated


DEFAULT_NDGS = ["Device Type#All Device Types", "Location#All Locations",
                "IPSEC#Is IPSEC Device#No"]
CUSTOM_NDGS = ["Device Type#All Device Types#Wired#Access Switchs#G4",
               "Location#All Locations#EMEA#STO", "IPSEC#Is IPSEC Device#No"]


def test_mode_off_skips_without_any_lookup():
    result, updated = _run("off", DEFAULT_NDGS)
    assert result["status"] == "skipped"
    assert "disabled" in result["message"]
    assert updated == []


def test_mode_defaults_only_updates_default_device():
    result, updated = _run("defaults-only", DEFAULT_NDGS)
    assert result["status"] == "success"
    assert len(updated) == 1


def test_mode_defaults_only_skips_configured_device():
    result, updated = _run("defaults-only", CUSTOM_NDGS)
    assert result["status"] == "skipped"
    assert "non-default" in result["message"]
    assert updated == []


def test_mode_overwrite_updates_configured_device():
    result, updated = _run("overwrite", CUSTOM_NDGS)
    assert result["status"] == "success"
    assert len(updated) == 1
    assert "Device Type#All Device Types#Wired" in updated[0]
    assert "IPSEC#Is IPSEC Device#No" in updated[0]


def test_manual_trigger_ignores_webhook_mode():
    ss.set_setting("webhook.update_mode", "off")
    try:
        result, updated = None, []
        ise = AsyncMock()
        ise.__aenter__ = AsyncMock(return_value=ise)
        ise.__aexit__ = AsyncMock(return_value=False)
        ise.find_device = AsyncMock(return_value=_ise_device(DEFAULT_NDGS))

        async def fake_update(device):
            updated.append(1)
            return {}
        ise.update_device = fake_update
        cc = AsyncMock()
        cc.__aenter__ = AsyncMock(return_value=cc)
        cc.__aexit__ = AsyncMock(return_value=False)
        plan = {"matched": True, "device": CC_DEVICE,
                "rule": {"id": 1, "description": "t", "priority": 10},
                "targets": {"device_type": "Device Type#All Device Types#Wired",
                            "location": None, "create_location": False, "notes": []}}
        with patch.object(sync, "CatalystClient", return_value=cc), \
             patch.object(sync, "ISEClient", return_value=ise), \
             patch.object(sync, "enrich_from_cc", AsyncMock(return_value=dict(CC_DEVICE))), \
             patch.object(sync, "plan_for_device", return_value=plan):
            result = asyncio.run(sync.process_device_event(
                {"hostname": "X"}, trigger="manual", retry_lookup=False))
        assert result["status"] == "success"
        assert updated == [1]
    finally:
        ss.set_setting("webhook.update_mode", "overwrite")
