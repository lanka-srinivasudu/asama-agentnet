"""Encryption for third-party secrets companies give us (LLM, web-search and database keys).

Fernet (AES-128-CBC + HMAC-SHA256) with a key derived from AGENTNET_SECRET_KEY.
Plaintext keys are decrypted only at the moment an agent calls the provider; the API
never returns them, only a masked hint such as "sk-p…9fQ2".
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger("agentnet.crypto")
_DEV_SECRET = "agentnet-dev-secret-change-me"


def _fernet() -> Fernet:
    secret = os.getenv("AGENTNET_SECRET_KEY", _DEV_SECRET)
    if secret == _DEV_SECRET:
        log.warning("AGENTNET_SECRET_KEY not set - using the development key")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def encrypt(plaintext: str | None) -> str | None:
    if not plaintext:
        return None
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str | None) -> str | None:
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        log.error("could not decrypt a stored secret (was AGENTNET_SECRET_KEY rotated?)")
        return None


def mask(plaintext: str | None) -> str | None:
    if not plaintext:
        return None
    if len(plaintext) <= 8:
        return "••••"
    return f"{plaintext[:4]}…{plaintext[-4:]}"
