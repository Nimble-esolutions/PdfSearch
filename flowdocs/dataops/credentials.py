"""AES-GCM protection for optional stored object-store credentials."""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.core.exceptions import ImproperlyConfigured


def _key(raw: str | bytes | None = None) -> bytes:
    value = raw if raw is not None else os.getenv("DATAOPS_CONFIG_ENCRYPTION_KEY", "")
    if isinstance(value, str):
        try:
            value = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        except Exception as exc:
            raise ImproperlyConfigured("DATAOPS_CONFIG_ENCRYPTION_KEY must be urlsafe base64") from exc
    if len(value) not in (16, 24, 32):
        raise ImproperlyConfigured("DATAOPS_CONFIG_ENCRYPTION_KEY must decode to 16, 24 or 32 bytes")
    return value


def encrypt(value: str, *, aad: str = "dataops", key: str | bytes | None = None) -> tuple[str, str]:
    nonce = os.urandom(12)
    ciphertext = AESGCM(_key(key)).encrypt(nonce, value.encode("utf-8"), aad.encode("utf-8"))
    return base64.urlsafe_b64encode(ciphertext).decode("ascii"), base64.urlsafe_b64encode(nonce).decode("ascii")


def decrypt(ciphertext: str, nonce: str, *, aad: str = "dataops", key: str | bytes | None = None) -> str:
    try:
        raw = AESGCM(_key(key)).decrypt(
            base64.urlsafe_b64decode(nonce), base64.urlsafe_b64decode(ciphertext), aad.encode("utf-8")
        )
    except Exception as exc:
        raise ImproperlyConfigured("Unable to decrypt stored Data Operations credential") from exc
    return raw.decode("utf-8")
