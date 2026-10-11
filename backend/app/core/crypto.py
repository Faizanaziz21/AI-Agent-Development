"""Envelope for secrets at rest. Production: supply AGENTOS_SECRETS_KEY from KMS / Vault."""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


@lru_cache
def _fernet() -> Fernet:
    s = get_settings()
    key = s.secrets_key
    if not key:
        if s.env == "production":
            raise RuntimeError("AGENTOS_SECRETS_KEY must be set in production")
        key = base64.urlsafe_b64encode(hashlib.sha256(("agentos-dev:" + s.jwt_secret).encode()).digest()).decode()
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("secret could not be decrypted") from exc


def mask(value: str) -> str:
    if len(value) <= 6:
        return "••••"
    return value[:3] + "••••" + value[-2:]
