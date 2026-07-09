# Changelog

## 1.6.0 — 2026-07-08

### Fixed
- **Webhook device identification** (bug: license/assurance events were skipped
  with "no device reference found"): payload keys are now matched
  case-insensitively with `_`/`-` stripped, so `details.device_ip` is
  recognised like `deviceIp`. Additionally, a free-text fallback fishes the
  device out of description strings such as
  `"Registration succeeded for device 172.20.10.146 (SSTO146CIS.Global.web-int.net)"`
  — that exact CC LicenseManagement payload now resolves to IP + hostname and
  triggers the per-device update directly, no full reconciliation needed.

### Added
- **Webhook update mode** (Settings → Webhook, `WEBHOOK_UPDATE_MODE`) — choose
  what the per-device webhook update may do:
  - `overwrite` (default): apply the rule targets for Device Type and Location,
    even over existing non-default NDGs
  - `defaults-only`: only update devices still on the default NDGs
  - `off`: never update per device (log only; rely on reconciliation /
    the webhook→reconciliation trigger)
  The manual force-overwrite button and reconciliation are unaffected by this
  setting; every skip is visible in the audit log with its reason.

## 1.5.0 — 2026-07-07

### Added
- **Webhook debug panel** on the Dashboard: shows the exact destination URL to
  configure in Catalyst Center, whether a shared token is set, received/rejected
  counters (24 h), the last delivery time and the last 10 deliveries with
  status, parsed device and the full raw payload (expandable). Rejected
  deliveries (wrong/missing `X-Auth-Token`) are visible immediately.
- **Webhook → reconciliation trigger** (`webhook.trigger_reconcile`): when
  enabled, every valid webhook also schedules a reconciliation run after the
  debounce delay (bursts coalesce into one run). Useful when the CC event
  payload contains no usable device reference — the reconciliation then picks
  the device up anyway.
- **Force overwrite** (`POST /api/force-sync` + "⚡ Force overwrite in ISE"
  button in the Dry-run panel): applies the matching rule to one device NOW
  and overwrites its NDGs in ISE — bypasses reconciliation scope, the
  compliance cache and approval mode. Fails fast if the device is not in ISE
  (no retry backoff). Blacklisted devices are still refused. Result appears
  in the audit log with trigger `manual`.

## 1.4.0 — 2026-07-07

### Added
- **Reconciliation scope** (`reconcile.scope`): new setting *"Enforce rules on
  ALL devices"*. Until now reconciliation only touched devices still on the
  default NDGs (`Device Type#All Device Types` / `Location#All Locations`) —
  a device with non-default but *wrong* NDGs was considered "already
  configured" and skipped, even though dry-run showed a rule would change it.
  With scope = all, every device is checked against the rules and corrected
  (or queued for approval) when its NDGs differ from what its rule prescribes.
  Default remains "defaults only".

### Changed
- The device cache now tracks **rule compliance** instead of just
  "has default NDGs": devices with no action needed (unmatched, not in CC,
  excluded, already correct) are skipped until the re-check interval expires —
  periodic runs no longer re-query Catalyst Center for the same unmatched
  devices every 30 minutes.
- **Any rule/site-mapping change (or scope change / config import) invalidates
  the cache**, so the next run re-evaluates every device immediately — no
  waiting for the re-check interval after editing rules.
- Catalyst Center tag memberships are cached per run (one lookup per tag,
  not per device).

## 1.3.0 — 2026-07-07

### Fixed
- **NDG refresh failed on every run after the first**
  (`UPDATE statement on table 'ndg_cache' expected to update N row(s); 0 were
  matched`): the stale-entry prune ran before the row updates were written and
  deleted the entire cache mid-transaction. Pruning now happens after an
  explicit flush and compares NDG names instead of timestamps; an empty answer
  from ISE never wipes the cache.
- **ISE TrustSec `coaSourceHost` PUT rejection** (HTTP 400 "must be a valid
  value of node type Standalone/PPAN/Policy with Session services", seen on
  ISE 3.4): when ISE rejects its own stored value, the PUT is retried once
  with only that field removed so ISE falls back to its default. All other
  TrustSec settings are preserved. This unblocks devices that could never be
  updated.

### Added
- **Per-run device lists** on the Reconciliation page: each run row now has
  expandable lists showing exactly WHICH devices were fixed / queued as
  pending / unmatched (no rule) / not found in CC / excluded / errored — no
  more guessing why a device wasn't updated.
- One more Catalyst Center hostname-lookup fallback (`.*name.*` contains
  match) for inventories where the ISE name is embedded in the CC FQDN.

## 1.2.0 — 2026-07-07

### Added
- **Per-device blacklist**: mark individual devices that must never be touched.
  - Manage entries on the Reconciliation page (name and/or IP + note); name
    matches FQDN and short hostname in both directions, IP matches exactly,
    all case-insensitive.
  - One-click **Blacklist** button on a pending change (rejects it and
    blacklists the device in one step).
  - Enforced in both the webhook pipeline (audit log shows
    "device is blacklisted") and reconciliation (counted as "Excluded").
  - API: `GET/POST /api/blacklist`, `DELETE /api/blacklist/{id}`,
    `POST /api/pending/{id}/blacklist`.

## 1.1.0 — 2026-07-07

Field-test feedback release (tested against ISE 3.2.0.542 / 3.4.0.608 and
Catalyst Center 2.3.7).

### Fixed
- **Case-insensitive matching** for all rule criteria, tags and site mappings —
  a rule pattern like `ssto146.*` now matches hostname `SSTO146CIS.Global.web-int.net`.
- **ISE 3.4 malformed 401 responses** (`illegal header line: "Unauthorized User: : 401"`,
  emitted when ISE throttles ERS) are retried and wrapped; a single bad response
  no longer aborts an entire reconciliation run — per-device errors are counted
  and the scan continues.
- **docker-compose**: `PORT=8090` is now set to match the `8090:8090` port mapping.

### Added
- **Manual approval mode** (`reconcile.mode = approve`): reconciliation queues
  proposed changes instead of writing to ISE; approve/reject them per device
  (or all at once) on the Reconciliation page.
- **ISE device state cache**: compliant devices are skipped for
  `reconcile.detail_ttl_hours` (default 24 h) — periodic runs over thousands of
  devices finish in seconds instead of minutes.
- **Device exclusion list** (`reconcile.exclude`): regex patterns per line,
  matched case-insensitively against ISE device name and IP.
- **Device lookup fallbacks**: FQDN ↔ short hostname ↔ management IP in both
  directions (CC and ISE inventories often disagree on naming).
- App version shown in the GUI header and `/healthz`.

### Changed
- Persistent keep-alive HTTP connections for the ISE and Catalyst Center
  clients (one TLS handshake + auth per run instead of per call).
- Reconciliation run history now tracks `pending` and `excluded` counts
  (existing `/data` volumes are migrated automatically).

## 1.0.0 — 2026-07-06

Initial release: webhook listener with per-device debounce, mapping-rule engine
(first match wins), site→location derivation, NDG cache, reconciliation job,
web GUI (settings, rules, dry-run simulator, audit log with CSV export),
Fernet-encrypted credentials, env-var overrides, Docker/Podman packaging.
