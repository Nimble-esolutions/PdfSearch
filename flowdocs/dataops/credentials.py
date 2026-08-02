"""AES-GCM protection for optional stored object-store credentials."""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Mapping

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone


class CredentialConfigurationError(ImproperlyConfigured):
    """A profile credential reference is missing or internally inconsistent."""


@dataclass(frozen=True)
class ResolvedCredentials:
    access_key: str
    secret_key: str
    source: str
    reference: str


def _key(raw: str | bytes | None = None) -> bytes:
    value = raw if raw is not None else os.getenv("DATAOPS_CONFIG_ENCRYPTION_KEY", "")
    if isinstance(value, str):
        try:
            value = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        except Exception as exc:
            raise ImproperlyConfigured("DATAOPS_CONFIG_ENCRYPTION_KEY must be urlsafe base64") from exc
    if len(value) != 32:
        raise ImproperlyConfigured("DATAOPS_CONFIG_ENCRYPTION_KEY must decode to 32 bytes (AES-256)")
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


def _reference(profile) -> str:
    value = str(getattr(profile, "credential_ref", "") or getattr(profile, "credential_prefix", "") or "").strip()
    if value.startswith("environment:"):
        value = value.split(":", 1)[1]
    return value


def resolve_profile_credentials(
    profile,
    *,
    environ: Mapping[str, str] | None = None,
    stored=None,
) -> ResolvedCredentials:
    """Resolve ENV credentials first, then an encrypted DB fallback.

    The returned object is for the in-process storage client only.  Callers
    must not serialize it or put it in operation checkpoints.
    """
    env = os.environ if environ is None else environ
    reference = _reference(profile)
    if not reference:
        raise CredentialConfigurationError("credential_reference_missing")
    access_key = str(env.get(f"{reference}_ACCESS_KEY", "") or "").strip()
    secret_key = str(env.get(f"{reference}_SECRET_KEY", "") or "").strip()
    if bool(access_key) != bool(secret_key):
        raise CredentialConfigurationError("credential_reference_mismatch")
    if access_key and secret_key:
        return ResolvedCredentials(access_key, secret_key, "environment", reference)

    record = stored
    if record is None:
        try:
            from django.apps import apps

            if apps.ready:
                model = apps.get_model("dataops", "DataProfile")
                credential_model = apps.get_model("dataops", "DataCredential")
                profile_row = model.objects.using("control").filter(key=profile.key).first()
                if profile_row is not None:
                    record = credential_model.objects.using("control").filter(profile_id=profile_row.pk, enabled=True).first()
        except Exception:
            record = None
    if record is None or not getattr(record, "access_key_ciphertext", "") or not getattr(record, "secret_ciphertext", ""):
        raise CredentialConfigurationError("credential_reference_unavailable")
    aad = f"profile:{profile.key}"
    return ResolvedCredentials(
        decrypt(record.access_key_ciphertext, getattr(record, "access_key_nonce", "") or record.nonce, aad=aad),
        decrypt(record.secret_ciphertext, getattr(record, "secret_nonce", "") or record.nonce, aad=aad),
        "stored",
        reference,
    )


def store_profile_credentials(profile, access_key: str, secret_key: str, *, key=None):
    """Replace encrypted fallback credentials without ever returning plaintext."""
    if not access_key.strip() or not secret_key.strip():
        raise CredentialConfigurationError("credential_values_incomplete")
    from django.apps import apps

    model = apps.get_model("dataops", "DataCredential")
    aad = f"profile:{profile.key}"
    access_ciphertext, access_nonce = encrypt(access_key.strip(), aad=aad, key=key)
    secret_ciphertext, secret_nonce = encrypt(secret_key.strip(), aad=aad, key=key)
    record, _ = model.objects.using("control").update_or_create(
        profile_id=profile.pk,
        defaults={
            "access_key_ciphertext": access_ciphertext,
            "secret_ciphertext": secret_ciphertext,
            "access_key_nonce": access_nonce,
            "secret_nonce": secret_nonce,
            "nonce": "",
            "enabled": True,
            "last_rotated_at": timezone.now(),
        },
    )
    return record
