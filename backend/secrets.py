"""Persist credentials encrypted; API projections never include secret columns."""
import hashlib
import os

from cryptography.fernet import Fernet


def credential_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def cipher():
    key = os.environ.get("CONFIG_ENCRYPTION_KEY", "")
    if not key:
        raise RuntimeError("Run scripts/upgrade_config.py to add CONFIG_ENCRYPTION_KEY")
    return Fernet(key.encode())


def encrypt(value):
    return cipher().encrypt(value.encode()).decode()


def decrypt(value):
    return cipher().decrypt(value.encode()).decode() if value else ""
