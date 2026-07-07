# ise-ndg-sync

**Catalyst Center → Cisco ISE Network Device Group Sync**

When Cisco Catalyst Center provisions a new network device it pushes the device into
Cisco ISE — but always with the **default** Network Device Groups
(`Device Type#All Device Types`, `Location#All Locations`), so after Day-N deployment
devices can't authenticate against the right policy. This container closes that gap:

1. It listens for the Catalyst Center **webhook** when a device is provisioned/added.
2. It looks the device up in Catalyst Center (hostname, family, series, platform, site, tags).
3. It applies your **mapping rules** (first match wins).
4. It updates the device in ISE with the correct **Device Type** and **Location** NDG —
   touching *only* those two dimensions (IPSEC and custom dimensions are preserved).
5. A periodic **reconciliation job** self-heals devices that are still on the defaults
   (missed webhooks, CC re-provisioning).

Everything is managed through a built-in **web GUI**: credentials, mapping rules,
live NDG dropdowns from ISE, site→location mapping, dry-run simulator,
reconciliation view and a full audit log with CSV export.

---

## Quick start

```bash
git clone <this repo> && cd cc_ise_dayn_sync
docker compose up -d --build
# GUI: http://<host>:8080
```

All state (SQLite DB + encryption key) lives in the `./data` volume (`/data` in the container).

> **Podman / quadlet:** the compose file is a single service with one volume and one port —
> `podman-compose up -d` works as-is, or generate a quadlet from the same image
> (`podman run -d --name ise-ndg-sync -p 8090:8090 -v ./data:/data ise-ndg-sync:latest`).

### TLS / reverse proxy (important)

The container serves plain HTTP on port 8090 (GUI **and** webhook on the same port).
Catalyst Center webhooks must target **HTTPS**, so put the container behind a TLS
reverse proxy — e.g. Nginx Proxy Manager, Traefik or Caddy:

```
Catalyst Center ──HTTPS──▶ reverse proxy (TLS) ──HTTP──▶ ise-ndg-sync:8080
```

Forward the whole host (or at minimum `/webhook/...`) to the container. No special
headers are required; the webhook is authenticated with a shared token.

---

## Prerequisites

### Cisco ISE

Tested against ISE 3.1, 3.2 (3.2.0.542) and 3.4 (3.4.0.608). Note: some ISE
builds answer with a malformed HTTP header (`Unauthorized User: : 401`) when
they throttle ERS requests — the client retries these and keeps one pooled
connection per run so large scans neither trip the throttle nor abort.

1. **Enable ERS:** *Administration → System → Settings → API Settings →
   ERS (Read/Write) = Enabled*.
2. **API user:** create an admin user with the **ERS Admin** role
   (or use OpenAPI on ISE 3.1+ and enable *Open API* in the same settings page —
   select the flavor in the GUI).
3. The ERS port (default **9060**) must be reachable from the container.

### Catalyst Center

1. An API user for the Intent API (read access to device inventory, sites and tags).
2. **Webhook subscription** (done in the CC GUI):
   *Platform → Developer Toolkit → Event Notifications → Notifications → new REST webhook*
   - **URL:** `https://<your-proxy>/webhook/catalystcenter` (or your configured path)
   - **Headers:** `X-Auth-Token: <shared token from the Settings page>`
   - **Events to subscribe:** device provisioning / inventory events, e.g.
     - *Device added to inventory* (`NETWORK-DEVICES-…`)
     - *Provisioning success / Device provisioned*
     - *ISE integration* related events (optional)
   - Payload formats vary per event — the listener parses defensively and extracts
     `networkDeviceId` / `hostname` / `managementIpAddress` from wherever they appear;
     the raw payload is always stored in the audit log.

---

## First-run walkthrough

1. Open `http://<host>:8080` → **Settings**.
2. Enter Catalyst Center base URL + credentials → **Test connection** → green.
3. Enter ISE base URL + ERS credentials (flavor *ERS*, port 9060) → **Test connection** → green.
4. Set a **webhook shared token** and save.
5. Go to **NDGs** → **Refresh from ISE** → device types and locations appear.
6. Go to **Site Mappings** and map your CC site hierarchies to ISE Location NDGs, e.g.
   `^Global/DE/Schierling/.*` → `Location#All Locations#DE#Schierling`.
7. Go to **Mapping Rules** → **New rule**, e.g.:
   - Hostname pattern: `^SW-SCH-.*`
   - Device Type NDG: `Device Type#All Device Types#Wired#Access Switchs#G1`
   - Location mode: *Derive from CC site* (fallback: skip / default / auto-create)
8. Use **Dry-run / Simulate** with a device from CC inventory to verify which rule
   matches and what would be written to ISE — nothing is written in dry-run.
9. Subscribe the webhook in Catalyst Center (see above). Done — provision a device
   and watch the **Audit Log**.

The **Reconciliation** page shows the periodic safety-net job (default every 30 min):
it scans ISE for devices still on `Device Type#All Device Types` or
`Location#All Locations` and re-applies the rules. "Run now" triggers it manually.

Reconciliation controls (Settings → Jobs):

- **Manual approval mode** — when enabled, reconciliation never writes to ISE
  directly; proposed changes are queued on the Reconciliation page where you
  approve or reject them per device (or "Approve all"). Recommended for the
  first runs to avoid unwanted mass changes.
- **Device re-check interval** — compliant devices are cached locally
  (`ise_device_cache`) and skipped for this many hours (default 24), so a run
  over thousands of devices only fetches details for new, changed or
  non-compliant devices instead of taking minutes every time.
- **Exclusions** — regex patterns (one per line, case-insensitive, matched
  against the ISE device name and IP) for devices reconciliation must never
  touch.
- **Device blacklist** (Reconciliation page) — mark individual devices that
  must never be modified by webhook or reconciliation. Add them by name/IP,
  or click **Blacklist** directly on a pending change to reject it and ban
  the device in one step.

All rule/site matching is **case-insensitive** (CC and ISE often disagree on
hostname casing), and device lookups try the FQDN, the short hostname and the
management IP in both directions.

---

## How updates are written to ISE

The updater follows the ERS contract strictly:

1. `GET /ers/config/networkdevice?filter=name.EQ.<hostname>` (fallback: by IP).
   If the device is not in ISE yet (CC may still be pushing it), it retries with
   backoff: 30 s → 60 s → 120 s → 300 s → 900 s (configurable), then marks it failed.
2. `GET /ers/config/networkdevice/<id>` → the **complete** object.
3. Only the `Device Type#…` and `Location#…` entries of `NetworkDeviceGroupList`
   are replaced; `IPSEC#…` and any custom dimensions stay untouched.
4. `PUT /ers/config/networkdevice/<id>` with the full object (ERS requires all fields).

Every action is recorded in the audit log with before/after NDGs, the matched rule
and HTTP results.

---

## Configuration reference (env vars)

GUI-set values persist in SQLite; environment variables **override** them (12-factor)
and show as read-only in the GUI.

| Variable | Default | Description |
|---|---|---|
| `CC_BASE_URL` | – | Catalyst Center base URL |
| `CC_USERNAME` / `CC_PASSWORD` | – | Intent API credentials |
| `CC_VERIFY_TLS` | `false` | Verify CC TLS certificate |
| `ISE_BASE_URL` | – | ISE base URL / hostname |
| `ISE_ERS_PORT` | `9060` | ERS port |
| `ISE_USERNAME` / `ISE_PASSWORD` | – | ERS/OpenAPI credentials |
| `ISE_VERIFY_TLS` | `false` | Verify ISE TLS certificate |
| `ISE_API_FLAVOR` | `ers` | `ers` or `openapi` (ISE 3.1+) |
| `WEBHOOK_TOKEN` | – | Shared secret CC must send as `X-Auth-Token` |
| `WEBHOOK_PATH` | `/webhook/catalystcenter` | Webhook listen path (must be under `/webhook/`) |
| `SYNC_DEBOUNCE_SECONDS` | `60` | Coalesce window per device |
| `SYNC_RETRY_SCHEDULE` | `30,60,120,300,900` | ISE lookup backoff (seconds) |
| `NDG_REFRESH_HOURS` | `6` | NDG cache refresh interval |
| `RECONCILE_ENABLED` | `true` | Enable the reconciliation job |
| `RECONCILE_MINUTES` | `30` | Reconciliation interval |
| `RECONCILE_MODE` | `auto` | `auto` = apply immediately, `approve` = queue for manual approval |
| `RECONCILE_EXCLUDE` | – | Regex per line, ISE devices to skip (name/IP, case-insensitive) |
| `RECONCILE_DETAIL_TTL_HOURS` | `24` | How long compliant devices are skipped via the local cache |
| `UI_ADMIN_PASSWORD` | – | Enables GUI login (user `admin`); empty = open |
| `LOG_LEVEL` | `INFO` | stdout log level |
| `PORT` | `8080` | Listen port |
| `DATA_DIR` | `/data` | State directory |

Secrets are stored **Fernet-encrypted** in `/data/app.db`; the key is generated on
first start at `/data/.secret` (mode 0600). Passwords are never logged and are
masked in the GUI after saving. All outbound calls use a 15 s timeout with
exponential-backoff retries and respect the per-system verify-TLS toggle.

## Operations

- **Healthcheck:** `GET /healthz` → `{"status":"ok","cc_reachable":true,"ise_reachable":true}`
  (also wired as the Docker `HEALTHCHECK`).
- **Backup/restore:** *Settings → Export config* downloads rules + site mappings +
  non-secret settings as JSON; *Import config* restores them.
- **Webhook test:** *Mapping Rules → Webhook payload test* replays a sample
  payload through the parser + rule engine without writing to ISE.
- **Logs:** stdout, container-friendly, level via `LOG_LEVEL`.

## Development

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest
DATA_DIR=./data uvicorn app.main:app --reload --port 8080
pytest tests/ -v
```
