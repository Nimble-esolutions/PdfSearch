"""Stable signing material for Data Operations v3 evidence.

Production-like environments require the explicit activation signing key so
application-secret rotation cannot silently orphan recovery points. Local
development and tests may fall back to Django's existing secret, avoiding a
second operator setting for disposable instances.
"""

from __future__ import annotations

import hashlib

from django.conf import settings


class V3SigningError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def manifest_signing_material() -> tuple[bytes, str]:
    value = str(getattr(settings, "ACTIVATION_INTENT_SIGNING_KEY", "") or "")
    environment = str(getattr(settings, "APP_ENV", "") or "").strip().lower()
    if not value and environment in {"development", "dev", "test"}:
        value = str(getattr(settings, "SECRET_KEY", "") or "")
    if not value:
        raise V3SigningError("manifest_signing_key_missing")
    key = value.encode("utf-8")
    return key, "dataops-manifest-" + hashlib.sha256(key).hexdigest()[:12]
