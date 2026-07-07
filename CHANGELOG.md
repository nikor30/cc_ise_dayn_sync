# Changelog

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
