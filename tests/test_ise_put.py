"""ISE update_device: TrustSec coaSourceHost 400 must trigger a sanitized retry."""
import asyncio
from unittest.mock import AsyncMock

import app.settings_store as ss
from app.db import init_db
from app.clients.ise import ISEClient, ISEError

init_db()
ss.set_setting("ise.base_url", "https://ise.test.local")

DEVICE = {
    "id": "abc", "name": "SWUH032CIS",
    "link": {"rel": "self"},
    "trustsecsettings": {
        "deviceAuthenticationSettings": {"sgaDeviceId": "SWUH032CIS"},
        "sgaNotificationAndUpdates": {"coaSourceHost": "old-psn-node",
                                      "sendConfigurationToDevice": False},
    },
    "NetworkDeviceGroupList": ["Device Type#All Device Types"],
}


class FakeResp:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text

    def json(self):
        return {}


def test_coasourcehost_400_retries_without_field():
    ise = ISEClient()
    calls = []

    async def fake_req(method, url, json_body=None, params=None):
        calls.append(json_body)
        if len(calls) == 1:
            return FakeResp(400, '{"ERSResponse": {"messages": [{"title": "Validation Error - '
                                 'Illegal values: [trustsecsettings: sgaNotificationAndUpdates: '
                                 'coaSourceHost must be a valid value of node type '
                                 'Standalone/PPAN/Policy with Session services]"}]}}')
        return FakeResp(200)

    ise._req = fake_req
    asyncio.run(ise.update_device(dict(DEVICE)))
    assert len(calls) == 2
    first, second = calls[0]["NetworkDevice"], calls[1]["NetworkDevice"]
    assert "link" not in first  # link is always stripped
    assert first["trustsecsettings"]["sgaNotificationAndUpdates"]["coaSourceHost"] == "old-psn-node"
    sga = second["trustsecsettings"]["sgaNotificationAndUpdates"]
    assert "coaSourceHost" not in sga
    assert sga["sendConfigurationToDevice"] is False  # other TrustSec fields kept


def test_other_400_is_not_retried():
    ise = ISEClient()
    calls = []

    async def fake_req(method, url, json_body=None, params=None):
        calls.append(json_body)
        return FakeResp(400, '{"ERSResponse": {"messages": [{"title": "some other error"}]}}')

    ise._req = fake_req
    try:
        asyncio.run(ise.update_device(dict(DEVICE)))
        assert False, "expected ISEError"
    except ISEError:
        pass
    assert len(calls) == 1
