"""Webhook debug endpoint, reconcile trigger and force-sync API."""
import asyncio
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

import app.settings_store as ss
from app.db import init_db
from app.main import app
from app import webhook

init_db()
client = TestClient(app)


def test_webhook_status_reports_deliveries():
    ss.set_setting("webhook.token", "")
    client.post("/webhook/catalystcenter", json={"details": {"hostname": "SW-DBG-1"}})
    status = client.get("/api/webhook/status").json()
    assert status["path"] == "/webhook/catalystcenter"
    assert status["received_24h"] >= 1
    assert status["last_received"] is not None
    assert any(e["device_name"] == "SW-DBG-1" for e in status["recent"])


def test_webhook_status_counts_rejected_tokens():
    ss.set_setting("webhook.token", "sekret")
    resp = client.post("/webhook/catalystcenter", json={"details": {"hostname": "SW-DBG-2"}})
    assert resp.status_code == 401
    status = client.get("/api/webhook/status", headers={}).json()
    assert status["rejected_24h"] >= 1
    assert status["token_set"] is True
    ss.set_setting("webhook.token", "")


def test_webhook_triggers_reconcile_when_enabled():
    ss.set_setting("webhook.trigger_reconcile", "true")
    ss.set_setting("sync.debounce_seconds", "60")
    try:
        resp = client.post("/webhook/catalystcenter",
                           json={"eventId": "X", "no_device": "here"})
        assert resp.json()["reconcile_triggered"] is True
        resp = client.post("/webhook/catalystcenter",
                           json={"details": {"hostname": "SW-DBG-3"}})
        assert resp.json()["reconcile_triggered"] is True
    finally:
        ss.set_setting("webhook.trigger_reconcile", "false")


def test_webhook_does_not_trigger_reconcile_by_default():
    resp = client.post("/webhook/catalystcenter", json={"details": {"hostname": "SW-DBG-4"}})
    assert resp.json()["reconcile_triggered"] is False


def test_debounced_action_coalesces():
    calls = []

    async def action():
        calls.append(1)

    async def scenario():
        trig = webhook.DebouncedAction(action, "t")
        trig.schedule(0)
        trig.schedule(0)  # supersedes the first
        await asyncio.sleep(0.05)

    asyncio.run(scenario())
    assert calls == [1]


def test_force_sync_requires_ref():
    assert client.post("/api/force-sync", json={}).status_code == 400


def test_force_sync_respects_blacklist():
    from app import blacklist
    entry = blacklist.add("SW-FORCE-BL", "", "test")
    try:
        resp = client.post("/api/force-sync", json={"hostname": "SW-FORCE-BL"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "skipped"
        assert "blacklisted" in resp.json()["message"]
    finally:
        blacklist.remove(entry["id"])
