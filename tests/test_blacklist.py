"""Per-device blacklist: matching semantics and API round-trip."""
from fastapi.testclient import TestClient

from app.db import init_db
from app.main import app
from app import blacklist

init_db()
client = TestClient(app)


def test_name_matches_fqdn_and_short_both_directions():
    entries = [{"name": "SSTO146CIS", "ip": ""}]
    assert blacklist.matches(entries, "ssto146cis.global.web-int.net")  # short entry vs FQDN
    assert blacklist.matches(entries, "SSTO146CIS")
    assert not blacklist.matches(entries, "SSTO142CIS.global.web-int.net")
    entries = [{"name": "swuh032cis.global.web-int.net", "ip": ""}]
    assert blacklist.matches(entries, "SWUH032CIS")  # FQDN entry vs short name


def test_ip_matches_exactly():
    entries = [{"name": "", "ip": "10.7.0.146"}]
    assert blacklist.matches(entries, "anything", "10.7.0.146")
    assert not blacklist.matches(entries, "anything", "10.7.0.14")
    assert not blacklist.matches(entries, "anything", "")


def test_add_remove_and_duplicate():
    entry = blacklist.add("SW-BL-1", "10.0.0.99", "test")
    assert entry["duplicate"] is False
    dup = blacklist.add("SW-BL-1", "10.0.0.99", "again")
    assert dup["duplicate"] is True and dup["id"] == entry["id"]
    assert blacklist.is_blacklisted("sw-bl-1.example.com")
    assert blacklist.remove(entry["id"])
    assert not blacklist.is_blacklisted("sw-bl-1.example.com")


def test_api_crud():
    resp = client.post("/api/blacklist", json={"name": "SW-API-BL", "note": "via api"})
    assert resp.status_code == 200
    entry_id = resp.json()["id"]
    names = [b["name"] for b in client.get("/api/blacklist").json()]
    assert "SW-API-BL" in names
    assert client.delete(f"/api/blacklist/{entry_id}").status_code == 200
    assert client.post("/api/blacklist", json={"note": "no device"}).status_code == 400


def test_pending_blacklist_rejects_and_blacklists():
    from app.reconcile import _queue_pending, list_pending
    _queue_pending("ise-bl", "SW-PEND-BL", "10.0.0.55", "#1 test",
                   ["Device Type#All Device Types"],
                   ["Device Type#All Device Types#Wired"], {"device_type": "x"})
    pid = [p for p in list_pending() if p["device_name"] == "SW-PEND-BL"][0]["id"]
    resp = client.post(f"/api/pending/{pid}/blacklist")
    assert resp.status_code == 200
    assert resp.json()["status"] == "blacklisted"
    assert not [p for p in list_pending() if p["id"] == pid]  # rejected
    assert blacklist.is_blacklisted("SW-PEND-BL")
    assert blacklist.is_blacklisted("", "10.0.0.55")


def test_webhook_pipeline_skips_blacklisted_device():
    import asyncio
    from app.sync import process_device_event
    blacklist.add("SW-WEBHOOK-BL", "", "test")
    result = asyncio.run(process_device_event({"hostname": "sw-webhook-bl.lab.local"}))
    assert result["status"] == "skipped"
    assert "blacklisted" in result["message"]
