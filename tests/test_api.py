"""API smoke tests with FastAPI TestClient (no external connectivity needed)."""
from fastapi.testclient import TestClient

from app.main import app
from app.db import init_db

init_db()
client = TestClient(app)


def test_healthz():
    resp = client.get("/healthz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "cc_reachable" in data and "ise_reachable" in data


def test_index_served():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "ise-ndg-sync" in resp.text


def test_settings_roundtrip_and_masking():
    resp = client.put("/api/settings", json={"cc.base_url": "https://cc.example.com",
                                             "cc.password": "supersecret"})
    assert resp.status_code == 200
    data = client.get("/api/settings").json()
    assert data["cc.base_url"]["value"] == "https://cc.example.com"
    assert data["cc.password"]["value"] == "••••••••"  # masked, never echoed
    # saving the mask back must not overwrite the stored secret
    client.put("/api/settings", json={"cc.password": "••••••••"})
    from app.settings_store import get_setting
    assert get_setting("cc.password") == "supersecret"


def test_rules_crud_and_reorder():
    r1 = client.post("/api/rules", json={"description": "one", "priority": 10,
                                         "match_hostname": "^SW-", "enabled": True,
                                         "device_type_ndg": "Device Type#All Device Types#Wired"}).json()
    r2 = client.post("/api/rules", json={"description": "two", "priority": 20}).json()
    rules = client.get("/api/rules").json()
    assert [r["description"] for r in rules[:2]] == ["one", "two"]
    client.post("/api/rules/reorder", json={"order": [r2["id"], r1["id"]]})
    rules = client.get("/api/rules").json()
    assert rules[0]["id"] == r2["id"]
    assert client.put(f"/api/rules/{r1['id']}", json={"enabled": False}).json()["enabled"] is False
    assert client.delete(f"/api/rules/{r1['id']}").status_code == 200
    client.delete(f"/api/rules/{r2['id']}")


def test_sitemap_crud():
    row = client.post("/api/sitemap", json={"site_pattern": "^Global/DE/.*",
                                            "location_ndg": "Location#All Locations#DE"}).json()
    assert client.get("/api/sitemap").json()[0]["site_pattern"] == "^Global/DE/.*"
    assert client.delete(f"/api/sitemap/{row['id']}").status_code == 200


def test_webhook_wrong_path_404():
    assert client.post("/webhook/nope", json={}).status_code == 404


def test_webhook_token_enforced():
    client.put("/api/settings", json={"webhook.token": "tok123"})
    resp = client.post("/webhook/catalystcenter", json={"details": {"hostname": "SW-1"}})
    assert resp.status_code == 401
    resp = client.post("/webhook/catalystcenter", headers={"X-Auth-Token": "tok123"},
                       json={"details": {"hostname": "SW-1"}})
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"
    client.put("/api/settings", json={"webhook.token": ""})


def test_webhook_no_device_ref_ignored():
    resp = client.post("/webhook/catalystcenter", json={"foo": "bar"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


def test_audit_and_csv():
    data = client.get("/api/audit").json()
    assert "items" in data and data["total"] >= 1  # webhook tests wrote entries
    resp = client.get("/api/audit.csv")
    assert resp.status_code == 200
    assert resp.text.startswith("id,timestamp,trigger")


def test_export_import_roundtrip():
    client.post("/api/rules", json={"description": "exported", "priority": 10})
    exported = client.get("/api/export").json()
    assert any(r["description"] == "exported" for r in exported["rules"])
    result = client.post("/api/import", json=exported).json()
    assert result["rules"] >= 1


def test_basic_auth_when_password_set():
    client.put("/api/settings", json={"ui.admin_password": "pw1"})
    try:
        assert client.get("/api/rules").status_code == 401
        assert client.get("/healthz").status_code == 200  # stays open
        assert client.get("/api/rules", auth=("admin", "pw1")).status_code == 200
    finally:
        client.put("/api/settings", json={"ui.admin_password": ""},
                   auth=("admin", "pw1"))
    assert client.get("/api/rules").status_code == 200
