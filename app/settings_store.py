"""Read/write settings with env override + encryption of secrets."""
import logging

from .config import DEFAULTS, SECRET_KEYS, env_override, as_bool
from .crypto import encrypt, decrypt
from .db import SessionLocal
from .models import Setting

log = logging.getLogger(__name__)
MASK = "••••••••"


def get_setting(key: str) -> str:
    env = env_override(key)
    if env is not None:
        return env
    with SessionLocal() as s:
        row = s.get(Setting, key)
        if row is not None:
            return decrypt(row.value) if row.encrypted else row.value
    return DEFAULTS.get(key, "")


def get_bool(key: str) -> bool:
    return as_bool(get_setting(key))


def get_int(key: str, fallback: int = 0) -> int:
    try:
        return int(str(get_setting(key)).strip())
    except (TypeError, ValueError):
        return fallback


def set_setting(key: str, value: str):
    if key not in DEFAULTS:
        raise KeyError(f"unknown setting: {key}")
    is_secret = key in SECRET_KEYS
    stored = encrypt(value) if is_secret else value
    with SessionLocal() as s:
        row = s.get(Setting, key)
        if row is None:
            row = Setting(key=key, value=stored, encrypted=is_secret)
            s.add(row)
        else:
            row.value = stored
            row.encrypted = is_secret
        s.commit()


def all_settings_masked() -> dict:
    """Settings for the GUI: secrets masked, env-overridden keys flagged."""
    out = {}
    for key in DEFAULTS:
        val = get_setting(key)
        if key in SECRET_KEYS and val:
            val = MASK
        out[key] = {"value": val, "env_override": env_override(key) is not None,
                    "secret": key in SECRET_KEYS}
    return out


def save_settings(payload: dict) -> list[str]:
    """Persist GUI-submitted settings. Masked secret values are left untouched.
    Returns the list of keys actually changed."""
    changed = []
    for key, value in payload.items():
        if key not in DEFAULTS:
            continue
        if key in SECRET_KEYS and value == MASK:
            continue  # unchanged masked secret
        if env_override(key) is not None:
            continue  # env wins; don't shadow it in the DB
        if get_setting(key) != value:
            set_setting(key, str(value))
            changed.append(key)
    return changed


def retry_schedule() -> list[int]:
    raw = get_setting("sync.retry_schedule")
    out = []
    for part in str(raw).split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return out or [30, 60, 120, 300, 900]
