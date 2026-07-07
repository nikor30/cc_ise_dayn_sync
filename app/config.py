"""Central configuration: defaults, env-var overrides and setting metadata.

Resolution order for every setting (12-factor friendly):
  1. Environment variable (ENV_MAP)   -- highest priority, read-only in GUI
  2. Value stored in SQLite (settings table, set via GUI)
  3. Built-in default (DEFAULTS)
"""
import os

APP_VERSION = "1.3.0"

DATA_DIR = os.environ.get("DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "app.db")
SECRET_PATH = os.path.join(DATA_DIR, ".secret")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
PORT = int(os.environ.get("PORT", "8080"))

DEFAULTS = {
    # Catalyst Center
    "cc.base_url": "",
    "cc.username": "",
    "cc.password": "",
    "cc.verify_tls": "false",
    # Cisco ISE
    "ise.base_url": "",
    "ise.ers_port": "9060",
    "ise.username": "",
    "ise.password": "",
    "ise.verify_tls": "false",
    "ise.api_flavor": "ers",  # "ers" | "openapi"
    # Webhook
    "webhook.token": "",
    "webhook.path": "/webhook/catalystcenter",
    # Sync behaviour
    "sync.debounce_seconds": "60",
    "sync.retry_schedule": "30,60,120,300,900",  # ISE lookup backoff, seconds
    # NDG cache refresh
    "ndg.refresh_hours": "6",
    # Reconciliation
    "reconcile.enabled": "true",
    "reconcile.minutes": "30",
    "reconcile.mode": "auto",  # "auto" = apply immediately | "approve" = queue for manual approval
    "reconcile.exclude": "",   # regex patterns (one per line) matched against ISE device name/IP
    "reconcile.detail_ttl_hours": "24",  # skip re-fetching compliant devices for this long
    # Optional GUI login (empty = auth disabled)
    "ui.admin_password": "",
}

# Values that are Fernet-encrypted at rest and masked in the GUI.
SECRET_KEYS = {"cc.password", "ise.password", "webhook.token", "ui.admin_password"}

ENV_MAP = {
    "CC_BASE_URL": "cc.base_url",
    "CC_USERNAME": "cc.username",
    "CC_PASSWORD": "cc.password",
    "CC_VERIFY_TLS": "cc.verify_tls",
    "ISE_BASE_URL": "ise.base_url",
    "ISE_ERS_PORT": "ise.ers_port",
    "ISE_USERNAME": "ise.username",
    "ISE_PASSWORD": "ise.password",
    "ISE_VERIFY_TLS": "ise.verify_tls",
    "ISE_API_FLAVOR": "ise.api_flavor",
    "WEBHOOK_TOKEN": "webhook.token",
    "WEBHOOK_PATH": "webhook.path",
    "SYNC_DEBOUNCE_SECONDS": "sync.debounce_seconds",
    "SYNC_RETRY_SCHEDULE": "sync.retry_schedule",
    "NDG_REFRESH_HOURS": "ndg.refresh_hours",
    "RECONCILE_ENABLED": "reconcile.enabled",
    "RECONCILE_MINUTES": "reconcile.minutes",
    "RECONCILE_MODE": "reconcile.mode",
    "RECONCILE_EXCLUDE": "reconcile.exclude",
    "RECONCILE_DETAIL_TTL_HOURS": "reconcile.detail_ttl_hours",
    "UI_ADMIN_PASSWORD": "ui.admin_password",
}

# reverse lookup: setting key -> env var name
KEY_TO_ENV = {v: k for k, v in ENV_MAP.items()}

HTTP_TIMEOUT = 15.0  # seconds, all outbound calls
HTTP_RETRIES = 3     # transient-error retries with exponential backoff


def env_override(key: str):
    """Return the env-var value for a setting key, or None."""
    env = KEY_TO_ENV.get(key)
    if env is not None:
        val = os.environ.get(env)
        if val is not None and val != "":
            return val
    return None


def as_bool(val) -> bool:
    return str(val).strip().lower() in ("1", "true", "yes", "on")
