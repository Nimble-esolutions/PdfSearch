"""Transactional profile mutations and redacted capability probes."""

from __future__ import annotations

import os
from urllib.parse import urlparse

from django.db import transaction
from django.utils import timezone

from .config import ADDRESSING_STYLES, PROVIDERS, _profile_key
from .credentials import store_profile_credentials
from .models import DataOpsAuditEvent, DataOpsSetting, DataProfile
from .storage import client_for_profile, probe_profile_access


class ProfileMutationError(ValueError):
    pass


SELECTOR_KEYS = {
    "backup": "DATAOPS_BACKUP_PROFILE",
    "restore": "DATAOPS_RESTORE_PROFILE",
    "backup_source": "DATAOPS_BACKUP_SOURCE_PROFILE",
    "backup_destination": "DATAOPS_BACKUP_DESTINATION_PROFILE",
    "restore_source": "DATAOPS_RESTORE_SOURCE_PROFILE",
    "restore_destination": "DATAOPS_RESTORE_DESTINATION_PROFILE",
}


def _bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def save_profile(values, *, actor=None):
    key = _profile_key(values.get("key", ""))
    provider = str(values.get("provider", "generic")).strip().lower()
    if provider not in PROVIDERS:
        raise ProfileMutationError("profile_provider_invalid")
    role = str(values.get("role", "both")).strip().lower()
    if role not in {"backup", "restore", "both"}:
        raise ProfileMutationError("profile_role_invalid")
    addressing_style = str(values.get("addressing_style", "auto")).strip().lower()
    if addressing_style not in ADDRESSING_STYLES:
        raise ProfileMutationError("profile_addressing_style_invalid")
    endpoint = str(values.get("endpoint", "")).strip().rstrip("/")
    if provider != "aws" and not endpoint:
        raise ProfileMutationError("profile_endpoint_required")
    if endpoint and (urlparse(endpoint).scheme not in {"http", "https"} or not urlparse(endpoint).hostname):
        raise ProfileMutationError("profile_endpoint_invalid")
    bucket = str(values.get("bucket", "")).strip()
    dataset_id = str(values.get("dataset_id", "")).strip()
    namespace = str(values.get("namespace", values.get("prefix", ""))).strip().strip("/")
    credential_ref = str(values.get("credential_ref", "")).strip()
    if not bucket or not dataset_id or not namespace or not credential_ref:
        raise ProfileMutationError("profile_required_fields_missing")

    with transaction.atomic(using="control"):
        existing = DataProfile.objects.using("control").filter(key=key).first()
        if existing and existing.environment_locked:
            raise ProfileMutationError("environment_profile_locked")
        profile, _ = DataProfile.objects.using("control").update_or_create(
            key=key,
            defaults={
                "display_name": str(values.get("display_name", "")).strip()[:160] or key.replace("-", " ").replace("_", " ").title(),
                "provider": provider,
                "role": role,
                "source": DataProfile.Source.STORED,
                "enabled": _bool(values.get("enabled"), True),
                "environment_locked": False,
                "endpoint": endpoint,
                "bucket": bucket,
                "region": str(values.get("region", "")).strip() or ("auto" if provider == "cloudflare_r2" else "us-east-1"),
                "dataset_id": dataset_id,
                "source_id": str(values.get("source_id", "")).strip(),
                "namespace": namespace,
                "prefix": namespace,
                "credential_ref": credential_ref,
                "credential_prefix": credential_ref,
                "addressing_style": addressing_style,
                "signature_version": "s3v4",
                "verify_tls": _bool(values.get("verify_tls"), True),
                "custom_ca_reference": str(values.get("custom_ca_reference", "")).strip(),
            },
        )
        access_key = str(values.get("access_key", ""))
        secret_key = str(values.get("secret_key", ""))
        if access_key or secret_key:
            store_profile_credentials(profile, access_key, secret_key)
        DataOpsAuditEvent.objects.using("control").create(
            actor_id=getattr(actor, "pk", None),
            actor_name=getattr(actor, "get_username", lambda: "")(),
            action="profile_saved",
            profile_key=key,
            evidence={"provider": provider, "role": role, "credential_source": "stored" if access_key else "reference"},
        )
    return profile


def save_selectors(values, *, actor=None):
    with transaction.atomic(using="control"):
        for short_name, setting_name in SELECTOR_KEYS.items():
            value = str(values.get(short_name, "")).strip().lower()
            DataOpsSetting.objects.using("control").update_or_create(key=setting_name, defaults={"value": value})
        DataOpsAuditEvent.objects.using("control").create(
            actor_id=getattr(actor, "pk", None),
            actor_name=getattr(actor, "get_username", lambda: "")(),
            action="profile_selectors_saved",
            evidence={"selector_keys": sorted(SELECTOR_KEYS.values())},
        )


def probe_profile(profile, resolved, *, actor=None):
    require_write = profile.role in {"backup", "both"}
    client = client_for_profile(resolved, os.environ, allow_http=resolved.endpoint.startswith("http://"))
    probe_profile_access(client, resolved, require_write=require_write)
    evidence = {
        "connect": True,
        "bucket": True,
        "list": True,
        "read": True,
        "write": require_write,
        "multipart": True,
        "observed_at": timezone.now().isoformat(),
    }
    DataProfile.objects.using("control").filter(pk=profile.pk).update(last_observed_at=timezone.now(), observation=evidence)
    DataOpsAuditEvent.objects.using("control").create(
        actor_id=getattr(actor, "pk", None), actor_name=getattr(actor, "get_username", lambda: "")(), action="profile_probed", profile_key=profile.key, evidence=evidence
    )
    return evidence
