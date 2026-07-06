"""Cisco ISE client — ERS (default) or OpenAPI (ISE 3.1+) flavor.

ERS:     Basic auth on every call, https://{ise}:9060/ers/config/...
OpenAPI: Basic auth,               https://{ise}/api/v1/...

ERS must be enabled (Administration > System > Settings > API Settings) and the
API user needs the "ERS Admin" role — surfaced as a hint in the GUI.
"""
import logging
from urllib.parse import urlparse

from ..http_utils import make_client, request_with_retry
from ..settings_store import get_setting, get_bool, get_int

log = logging.getLogger(__name__)

JSON_HEADERS = {"Accept": "application/json", "Content-Type": "application/json"}
PAGE_SIZE = 100


class ISEError(Exception):
    pass


class ISEClient:
    def __init__(self, base_url: str | None = None, username: str | None = None,
                 password: str | None = None, verify_tls: bool | None = None,
                 ers_port: int | None = None, flavor: str | None = None):
        raw = (base_url if base_url is not None else get_setting("ise.base_url")).rstrip("/")
        self.username = username if username is not None else get_setting("ise.username")
        self.password = password if password is not None else get_setting("ise.password")
        self.verify_tls = verify_tls if verify_tls is not None else get_bool("ise.verify_tls")
        self.ers_port = ers_port if ers_port is not None else get_int("ise.ers_port", 9060)
        self.flavor = (flavor if flavor is not None else get_setting("ise.api_flavor")).lower()
        if not raw:
            raise ISEError("ISE base URL is not configured")
        parsed = urlparse(raw if "://" in raw else f"https://{raw}")
        self.host = parsed.hostname or raw
        self.ers_base = f"https://{self.host}:{self.ers_port}/ers"
        self.openapi_base = f"https://{self.host}{f':{parsed.port}' if parsed.port and parsed.port not in (self.ers_port,) else ''}/api/v1"

    async def _req(self, method: str, url: str, json_body=None, params=None):
        async with make_client(self.verify_tls, auth=(self.username, self.password),
                               headers=JSON_HEADERS) as c:
            resp = await request_with_retry(c, method, url, json=json_body, params=params)
        return resp

    async def _get_json(self, url: str, params=None) -> dict:
        resp = await self._req("GET", url, params=params)
        if resp.status_code == 401:
            raise ISEError("ISE auth failed (401) — check credentials and the ERS Admin role")
        if resp.status_code == 404:
            return {}
        if resp.status_code >= 400:
            raise ISEError(f"ISE GET {url}: HTTP {resp.status_code} {resp.text[:300]}")
        try:
            return resp.json()
        except ValueError:
            raise ISEError(f"ISE GET {url}: non-JSON response (is ERS enabled?)")

    async def test_connection(self) -> dict:
        info = {"ok": True, "message": "Authenticated"}
        if self.flavor == "openapi":
            data = await self._get_json(f"{self.openapi_base}/network-device-group",
                                        params={"page": 1, "size": 1})
            if not data:
                raise ISEError("OpenAPI reachable but returned no data")
        else:
            await self._get_json(f"{self.ers_base}/config/networkdevicegroup",
                                 params={"size": 1, "page": 1})
            try:
                ver = await self._get_json(f"{self.ers_base}/config/op/systemconfig/iseversion")
                data = (ver or {}).get("OperationResult", {}).get("resultValue", [])
                for item in data:
                    if item.get("name") == "version":
                        info["version"] = item.get("value")
            except ISEError:
                pass
        return info

    # ---- Network Device Groups ---------------------------------------------
    async def list_ndgs(self) -> list[dict]:
        """All NDGs as [{'name':..., 'id':...}], paginated."""
        out = []
        if self.flavor == "openapi":
            page = 1
            while True:
                data = await self._get_json(f"{self.openapi_base}/network-device-group",
                                            params={"page": page, "size": PAGE_SIZE})
                items = data.get("response") if isinstance(data, dict) else data
                items = items or []
                out.extend({"name": i.get("name", ""), "id": i.get("id", "")} for i in items)
                if len(items) < PAGE_SIZE:
                    break
                page += 1
        else:
            page = 1
            while True:
                data = await self._get_json(f"{self.ers_base}/config/networkdevicegroup",
                                            params={"size": PAGE_SIZE, "page": page})
                res = (data or {}).get("SearchResult", {})
                items = res.get("resources", []) or []
                out.extend({"name": i.get("name", ""), "id": i.get("id", "")} for i in items)
                if not res.get("nextPage") or len(items) < PAGE_SIZE:
                    break
                page += 1
        return out

    async def create_ndg(self, name: str, root: str = "Location") -> str:
        """Create an NDG (used for auto-created Locations). Returns 'created'/'exists'."""
        body = {"NetworkDeviceGroup": {"name": name, "othername": root,
                                       "description": "auto-created by ise-ndg-sync"}}
        if self.flavor == "openapi":
            resp = await self._req("POST", f"{self.openapi_base}/network-device-group",
                                   json_body={"name": name, "othername": root,
                                              "description": "auto-created by ise-ndg-sync"})
        else:
            resp = await self._req("POST", f"{self.ers_base}/config/networkdevicegroup",
                                   json_body=body)
        if resp.status_code in (200, 201):
            return "created"
        if resp.status_code == 400 and "already exist" in resp.text.lower():
            return "exists"
        raise ISEError(f"ISE create NDG '{name}': HTTP {resp.status_code} {resp.text[:300]}")

    # ---- Network Devices -----------------------------------------------------
    async def list_devices(self) -> list[dict]:
        """Summary list [{'id':..., 'name':...}], paginated."""
        out, page = [], 1
        if self.flavor == "openapi":
            while True:
                data = await self._get_json(f"{self.openapi_base}/network-device",
                                            params={"page": page, "size": PAGE_SIZE})
                items = data.get("response") if isinstance(data, dict) else data
                items = items or []
                out.extend({"id": i.get("id", ""), "name": i.get("name", "")} for i in items)
                if len(items) < PAGE_SIZE:
                    break
                page += 1
        else:
            while True:
                data = await self._get_json(f"{self.ers_base}/config/networkdevice",
                                            params={"size": PAGE_SIZE, "page": page})
                res = (data or {}).get("SearchResult", {})
                items = res.get("resources", []) or []
                out.extend({"id": i.get("id", ""), "name": i.get("name", "")} for i in items)
                if not res.get("nextPage") or len(items) < PAGE_SIZE:
                    break
                page += 1
        return out

    async def get_device(self, device_id: str) -> dict | None:
        """Full NetworkDevice object (ERS shape) incl. NetworkDeviceGroupList."""
        if self.flavor == "openapi":
            data = await self._get_json(f"{self.openapi_base}/network-device/{device_id}")
            dev = data.get("NetworkDevice") or data or None
            return dev or None
        data = await self._get_json(f"{self.ers_base}/config/networkdevice/{device_id}")
        return (data or {}).get("NetworkDevice") or None

    async def _find_ers(self, filt: str) -> dict | None:
        data = await self._get_json(f"{self.ers_base}/config/networkdevice",
                                    params={"filter": filt})
        items = (data or {}).get("SearchResult", {}).get("resources", []) or []
        if not items:
            return None
        return await self.get_device(items[0]["id"])

    async def find_device(self, name: str = "", ip: str = "") -> dict | None:
        """Find device by name, falling back to IP. Returns the full object."""
        if self.flavor == "openapi":
            if name:
                dev = await self.get_device(name)  # OpenAPI addresses devices by name
                if dev:
                    return dev
            if ip:
                data = await self._get_json(f"{self.openapi_base}/network-device",
                                            params={"filter": f"ipaddress.EQ.{ip}"})
                items = data.get("response") if isinstance(data, dict) else data
                if items:
                    return await self.get_device(items[0].get("name") or items[0].get("id"))
            return None
        if name:
            dev = await self._find_ers(f"name.EQ.{name}")
            if dev:
                return dev
        if ip:
            return await self._find_ers(f"ipaddress.EQ.{ip}")
        return None

    async def update_device(self, device: dict) -> dict:
        """PUT the complete NetworkDevice object back (ERS requires all fields)."""
        device = {k: v for k, v in device.items() if k != "link"}
        if self.flavor == "openapi":
            name = device.get("name") or device.get("id")
            resp = await self._req("PUT", f"{self.openapi_base}/network-device/{name}",
                                   json_body=device)
        else:
            resp = await self._req("PUT", f"{self.ers_base}/config/networkdevice/{device['id']}",
                                   json_body={"NetworkDevice": device})
        if resp.status_code >= 400:
            raise ISEError(f"ISE PUT device '{device.get('name')}': "
                           f"HTTP {resp.status_code} {resp.text[:300]}")
        try:
            return resp.json()
        except ValueError:
            return {}
