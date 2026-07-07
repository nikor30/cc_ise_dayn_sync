"""Cisco Catalyst Center (DNA Center) Intent API client.

Auth: POST /dna/system/api/v1/auth/token with Basic auth -> {"Token": "..."},
then X-Auth-Token header on every Intent call (token cached ~50 min).
"""
import logging
import time

import httpx

from ..http_utils import make_client, request_with_retry
from ..settings_store import get_setting, get_bool

# The client keeps one pooled keep-alive connection per instance; reuse a
# single instance for bulk work (`async with CatalystClient() as cc:`) so the
# auth token and the tag list are fetched once, not per device.

log = logging.getLogger(__name__)

TOKEN_TTL = 50 * 60  # CC tokens live ~60 min; refresh after 50


class CatalystError(Exception):
    pass


class CatalystClient:
    def __init__(self, base_url: str | None = None, username: str | None = None,
                 password: str | None = None, verify_tls: bool | None = None):
        self.base_url = (base_url if base_url is not None else get_setting("cc.base_url")).rstrip("/")
        self.username = username if username is not None else get_setting("cc.username")
        self.password = password if password is not None else get_setting("cc.password")
        self.verify_tls = verify_tls if verify_tls is not None else get_bool("cc.verify_tls")
        self._token: str | None = None
        self._token_ts: float = 0.0
        self._tags_cache: list[dict] | None = None
        self._client: httpx.AsyncClient | None = None
        if not self.base_url:
            raise CatalystError("Catalyst Center base URL is not configured")

    def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = make_client(self.verify_tls)
        return self._client

    async def aclose(self):
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.aclose()

    async def _get_token(self) -> str:
        if self._token and time.monotonic() - self._token_ts < TOKEN_TTL:
            return self._token
        try:
            resp = await request_with_retry(self._http(), "POST",
                                            f"{self.base_url}/dna/system/api/v1/auth/token",
                                            auth=(self.username, self.password))
        except httpx.HTTPError as exc:
            raise CatalystError(f"CC auth request failed: {exc.__class__.__name__}: {exc}") from exc
        if resp.status_code != 200:
            raise CatalystError(f"CC auth failed: HTTP {resp.status_code}")
        token = resp.json().get("Token")
        if not token:
            raise CatalystError("CC auth response contained no Token")
        self._token, self._token_ts = token, time.monotonic()
        return token

    async def _get(self, path: str, params: dict | None = None) -> dict | list:
        headers = {"X-Auth-Token": await self._get_token(), "Accept": "application/json"}
        try:
            resp = await request_with_retry(self._http(), "GET", f"{self.base_url}{path}",
                                            params=params, headers=headers)
            if resp.status_code == 401:  # token expired early -> refresh once
                self._token = None
                headers["X-Auth-Token"] = await self._get_token()
                resp = await request_with_retry(self._http(), "GET", f"{self.base_url}{path}",
                                                params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise CatalystError(f"CC request failed: {exc.__class__.__name__}: {exc}") from exc
        if resp.status_code == 404:
            return {}
        if resp.status_code >= 400:
            raise CatalystError(f"CC GET {path}: HTTP {resp.status_code} {resp.text[:300]}")
        try:
            return resp.json()
        except ValueError:
            raise CatalystError(f"CC GET {path}: non-JSON response")

    async def test_connection(self) -> dict:
        await self._get_token()
        info = {"ok": True, "message": "Authenticated"}
        try:
            data = await self._get("/dna/intent/api/v1/network-device/count")
            info["device_count"] = (data or {}).get("response")
        except CatalystError:
            pass
        try:
            ver = await self._get("/dna/intent/api/v1/dnac-release")
            rel = (ver or {}).get("response") or {}
            if rel.get("displayVersion"):
                info["version"] = rel["displayVersion"]
        except CatalystError:
            pass
        return info

    # ---- devices -----------------------------------------------------------
    @staticmethod
    def _norm_device(d: dict) -> dict:
        return {
            "id": d.get("id") or d.get("instanceUuid") or "",
            "hostname": d.get("hostname") or "",
            "ip": d.get("managementIpAddress") or "",
            "family": d.get("family") or "",
            "series": d.get("series") or "",
            "platform": d.get("platformId") or "",
            "serial": d.get("serialNumber") or "",
        }

    async def get_device_by_id(self, device_id: str) -> dict | None:
        data = await self._get(f"/dna/intent/api/v1/network-device/{device_id}")
        dev = (data or {}).get("response")
        return self._norm_device(dev) if dev else None

    async def find_device(self, hostname: str = "", ip: str = "") -> dict | None:
        """Find a device by management IP or hostname. ISE stores the short name
        while CC inventory usually holds the FQDN (or vice versa), so try the
        exact name, then a wildcard prefix match on the short name."""
        candidates = []
        if ip:
            candidates.append({"managementIpAddress": ip})
        if hostname:
            candidates.append({"hostname": hostname})
            short = hostname.split(".")[0]
            if short and short.lower() != hostname.lower():
                candidates.append({"hostname": short})
            candidates.append({"hostname": f"{short or hostname}.*"})  # CC accepts regex here
            candidates.append({"hostname": f".*{short or hostname}.*"})
        for params in candidates:
            try:
                data = await self._get("/dna/intent/api/v1/network-device", params=params)
            except CatalystError:
                continue
            devs = (data or {}).get("response") or []
            if devs:
                return self._norm_device(devs[0])
        return None

    async def list_devices(self, limit: int = 500) -> list[dict]:
        out, offset = [], 1
        while True:
            data = await self._get("/dna/intent/api/v1/network-device",
                                   params={"offset": offset, "limit": limit})
            batch = (data or {}).get("response") or []
            out.extend(self._norm_device(d) for d in batch)
            if len(batch) < limit:
                return out
            offset += limit

    # ---- site hierarchy ----------------------------------------------------
    async def get_site(self, device_id: str) -> str:
        """Return the site hierarchy string, e.g. 'Global/DE/Schierling/Building1'."""
        try:
            data = await self._get("/dna/intent/api/v1/device-detail",
                                   params={"identifier": "uuid", "searchBy": device_id})
            detail = (data or {}).get("response") or {}
            for key in ("location", "siteHierarchy", "site"):
                if detail.get(key):
                    return str(detail[key])
        except CatalystError as exc:
            log.debug("device-detail lookup failed: %s", exc)
        # fallback: membership API
        try:
            data = await self._get(f"/dna/intent/api/v1/membership/{device_id}")
            for site in (data or {}).get("site", {}).get("response", []) or []:
                name = site.get("groupNameHierarchy") or site.get("name")
                if name:
                    return str(name)
        except CatalystError as exc:
            log.debug("membership lookup failed: %s", exc)
        return ""

    # ---- tags ---------------------------------------------------------------
    async def get_device_tags(self, device_id: str) -> list[str]:
        """Tags assigned to the device, via GET /tag + GET /tag/{id}/member.
        Tag list AND memberships are cached per client instance, so bulk runs
        (reconciliation) pay the member lookups once, not per device."""
        if self._tags_cache is None:
            data = await self._get("/dna/intent/api/v1/tag", params={"limit": 500})
            self._tags_cache = (data or {}).get("response") or []
        if not hasattr(self, "_tag_members"):
            self._tag_members: dict[str, set] = {}
        names = []
        for tag in self._tags_cache:
            tag_id, tag_name = tag.get("id"), tag.get("name")
            if not tag_id or not tag_name:
                continue
            if tag_id not in self._tag_members:
                try:
                    data = await self._get(f"/dna/intent/api/v1/tag/{tag_id}/member",
                                           params={"memberType": "networkdevice",
                                                   "limit": 500})
                    members = (data or {}).get("response") or []
                    self._tag_members[tag_id] = {
                        m.get("instanceUuid") or m.get("id") for m in members}
                except CatalystError:
                    self._tag_members[tag_id] = set()
            if device_id in self._tag_members[tag_id]:
                names.append(tag_name)
        return names
