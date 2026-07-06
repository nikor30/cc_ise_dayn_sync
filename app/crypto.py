"""Fernet encryption for credentials at rest.

The key is generated on first start and stored at /data/.secret (0600).
"""
import os
import logging

from cryptography.fernet import Fernet, InvalidToken

from .config import SECRET_PATH, DATA_DIR

log = logging.getLogger(__name__)
_fernet: Fernet | None = None


def get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        os.makedirs(DATA_DIR, exist_ok=True)
        if os.path.exists(SECRET_PATH):
            with open(SECRET_PATH, "rb") as fh:
                key = fh.read().strip()
        else:
            key = Fernet.generate_key()
            fd = os.open(SECRET_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(key)
            log.info("Generated new Fernet key at %s", SECRET_PATH)
        _fernet = Fernet(key)
    return _fernet


def encrypt(plaintext: str) -> str:
    if plaintext == "":
        return ""
    return get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    if ciphertext == "":
        return ""
    try:
        return get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        log.error("Could not decrypt a stored secret (key changed?); treating as empty")
        return ""
