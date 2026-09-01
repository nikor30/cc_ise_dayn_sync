"""ISE 'CSRF Check for Enhanced Security': writes must fetch + replay the token.

Reproduces the production failure on ISE 3.2.0.542: GETs work, PUTs die with
the malformed 'Unauthorized User: : 401' header until a CSRF token is sent.
"""
import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

import app.settings_store as ss
from app.db import init_db
from app.clients import ise as ise_mod
from app.clients.ise import ISEClient, ISEError

init_db()
ss.set_setting("ise.base_url", "https://ise.test.local")


class FakeResp:
    def __init__(self, status_code, text="", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}

    def json(self):
        return {}


def _csrf_ise(calls):
    """request_with_retry stand-in behaving like a CSRF-protected ISE 3.2."""
    async def fake(client, method, url, retries=3, **kw):
        headers = kw.get("headers") or {}
        calls.append((method, url, dict(headers)))
        if method == "GET" and "versioninfo" in url:
            return FakeResp(200, headers={"X-CSRF-Token": "tok-42"})
        if method in ("PUT", "POST", "DELETE"):
            if headers.get("X-CSRF-TOKEN") == "tok-42":
                return FakeResp(200)
            raise httpx.RemoteProtocolError(
                "illegal header line: bytearray(b'Unauthorized User: : 401')")
        return FakeResp(200)
    return fake


@pytest.fixture(autouse=True)
def _fast_and_clean(monkeypatch):
    monkeypatch.setattr(ise_mod, "_RETRY_PAUSE", 0)
    monkeypatch.setattr(ise_mod, "_csrf_detected", False)
    yield


def test_write_learns_csrf_and_retries(monkeypatch):
    calls = []
    monkeypatch.setattr(ise_mod, "request_with_retry", _csrf_ise(calls))
    client = ISEClient()
    client.aclose = AsyncMock()
    resp = asyncio.run(client._req("PUT", f"{client.ers_base}/config/networkdevice/x",
                                   json_body={"NetworkDevice": {}}))
    assert resp.status_code == 200
    methods = [(m, "versioninfo" in u, h.get("X-CSRF-TOKEN")) for m, u, h in calls]
    # 1) bare PUT fails -> 2) token fetch -> 3) PUT with token succeeds
    assert methods[0] == ("PUT", False, None)
    assert methods[1][0] == "GET" and methods[1][1] is True
    assert methods[2] == ("PUT", False, "tok-42")
    assert ise_mod._csrf_detected is True


def test_next_client_uses_csrf_proactively(monkeypatch):
    calls = []
    monkeypatch.setattr(ise_mod, "request_with_retry", _csrf_ise(calls))
    monkeypatch.setattr(ise_mod, "_csrf_detected", True)  # learned earlier
    client = ISEClient()
    client.aclose = AsyncMock()
    resp = asyncio.run(client._req("PUT", f"{client.ers_base}/config/networkdevice/x",
                                   json_body={}))
    assert resp.status_code == 200
    # token fetched BEFORE the first write — no failing attempt at all
    assert calls[0][0] == "GET" and "versioninfo" in calls[0][1]
    assert calls[1][0] == "PUT" and calls[1][2].get("X-CSRF-TOKEN") == "tok-42"


def test_clean_401_on_write_triggers_token_fetch(monkeypatch):
    calls = []

    async def fake(client, method, url, retries=3, **kw):
        headers = kw.get("headers") or {}
        calls.append((method, headers.get("X-CSRF-TOKEN")))
        if method == "GET" and "versioninfo" in url:
            return FakeResp(200, headers={"X-CSRF-Token": "tok-9"})
        if method == "PUT" and headers.get("X-CSRF-TOKEN") != "tok-9":
            return FakeResp(401, "CSRF nonce validation failed")
        return FakeResp(200)

    monkeypatch.setattr(ise_mod, "request_with_retry", fake)
    client = ISEClient()
    client.aclose = AsyncMock()
    resp = asyncio.run(client._req("PUT", f"{client.ers_base}/config/networkdevice/x"))
    assert resp.status_code == 200
    assert ("PUT", "tok-9") in calls


def test_persistent_malformed_read_raises_iseerror(monkeypatch):
    async def always_broken(client, method, url, retries=3, **kw):
        raise httpx.RemoteProtocolError("illegal header line")

    monkeypatch.setattr(ise_mod, "request_with_retry", always_broken)
    client = ISEClient()
    client.aclose = AsyncMock()
    try:
        asyncio.run(client._req("GET", f"{client.ers_base}/config/networkdevice"))
        assert False, "expected ISEError"
    except ISEError as exc:
        assert "throttling" in str(exc)
        assert "ERS Operator" not in str(exc)  # role hint is writes-only
    assert ise_mod._csrf_detected is False  # reads must not flip CSRF mode


def test_write_failure_message_names_readonly_role(monkeypatch):
    async def always_broken(client, method, url, retries=3, **kw):
        raise httpx.RemoteProtocolError("illegal header line")

    monkeypatch.setattr(ise_mod, "request_with_retry", always_broken)
    client = ISEClient()
    client.aclose = AsyncMock()
    try:
        asyncio.run(client._req("PUT", f"{client.ers_base}/config/networkdevice/x"))
        assert False, "expected ISEError"
    except ISEError as exc:
        assert "ERS Operator" in str(exc)
        assert "ERS Admin" in str(exc)
