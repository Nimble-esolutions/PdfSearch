import hashlib
import ipaddress
import json
import os
import re
import socket
from urllib.parse import urlsplit

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.artifact_vault import ArtifactVault, VaultConfig
from vaultops.models import VaultConnectionProfile


ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
ENV_PREFIX_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,79}$")
CGN_NETWORK = ipaddress.ip_network("100.64.0.0/10")


class VaultProfileError(RuntimeError):
    reason_code = "vault_profile_invalid"

    def __init__(self, reason_code=None):
        self.reason_code = reason_code or self.reason_code
        super().__init__(self.reason_code)


def _origin(endpoint):
    parsed = urlsplit((endpoint or "").strip())
    if (
        not parsed.scheme
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise VaultProfileError("vault_endpoint_invalid")
    if parsed.scheme not in {"https", "http"}:
        raise VaultProfileError("vault_endpoint_scheme_rejected")
    if (
        parsed.scheme == "http"
        and not settings.VAULT_ALLOW_HTTP_S3_ENDPOINTS
    ):
        raise VaultProfileError("vault_endpoint_https_required")
    try:
        port = parsed.port
    except ValueError as exc:
        raise VaultProfileError("vault_endpoint_port_invalid") from exc
    host = parsed.hostname.lower().rstrip(".")
    default_port = 443 if parsed.scheme == "https" else 80
    authority = host if not port or port == default_port else f"{host}:{port}"
    return f"{parsed.scheme}://{authority}", host, port or default_port


def _address_rejected(address):
    value = ipaddress.ip_address(address)
    return (
        value.is_loopback
        or value.is_private
        or value.is_link_local
        or value.is_multicast
        or value.is_reserved
        or value.is_unspecified
        or (value.version == 4 and value in CGN_NETWORK)
    )


def validate_endpoint(endpoint, *, resolver=None):
    """Validate an exact allowlisted endpoint and its current DNS answers."""
    origin, host, port = _origin(endpoint)
    allowed = set(settings.VAULT_ALLOWED_S3_ENDPOINTS)
    if origin not in allowed:
        raise VaultProfileError("vault_endpoint_not_allowlisted")
    resolver = resolver or socket.getaddrinfo
    try:
        answers = resolver(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise VaultProfileError("vault_endpoint_dns_failed") from exc
    addresses = {
        item[4][0].split("%", 1)[0]
        for item in answers
        if item and len(item) >= 5 and item[4]
    }
    if not addresses:
        raise VaultProfileError("vault_endpoint_dns_empty")
    if settings.VAULT_BLOCK_PRIVATE_S3_ENDPOINTS:
        try:
            rejected = any(_address_rejected(value) for value in addresses)
        except ValueError as exc:
            raise VaultProfileError("vault_endpoint_dns_invalid") from exc
        if rejected:
            raise VaultProfileError("vault_endpoint_private_address")
    return {
        "origin": origin,
        "hostname": host,
        "port": port,
        "addresses": sorted(addresses),
    }


def _credential_aliases():
    aliases = {}
    for raw in settings.VAULT_CREDENTIAL_ALIASES.split(","):
        item = raw.strip()
        if not item:
            continue
        alias, separator, prefix = item.partition("=")
        alias = alias.strip()
        prefix = prefix.strip()
        if (
            not separator
            or not ALIAS_RE.fullmatch(alias)
            or not ENV_PREFIX_RE.fullmatch(prefix)
            or alias in aliases
        ):
            raise VaultProfileError("credential_alias_configuration_invalid")
        aliases[alias] = prefix
    return aliases


def resolve_credentials(alias, *, environ=None):
    """Resolve one deployed alias without returning it to a browser model."""
    if alias == "environment:ARTIFACT_VAULT":
        config = VaultConfig.from_env(environ)
        return config.access_key, config.secret_key
    prefix = _credential_aliases().get(alias)
    if not prefix:
        raise VaultProfileError("credential_alias_not_approved")
    env = os.environ if environ is None else environ
    access_key = env.get(f"{prefix}_ACCESS_KEY", "").strip()
    secret_key = env.get(f"{prefix}_SECRET_KEY", "").strip()
    if not access_key or not secret_key:
        raise VaultProfileError("credential_alias_unavailable")
    return access_key, secret_key


def profile_fingerprint(profile):
    posture = {
        "endpoint_origin": profile.endpoint_origin.rstrip("/"),
        "bucket": profile.bucket,
        "region": profile.region,
        "dataset_id": profile.dataset_id,
        "production_source_id": profile.production_source_id,
        "credential_alias": profile.credential_alias,
        "read_only": bool(profile.read_only),
    }
    return hashlib.sha256(
        json.dumps(posture, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def materialize_environment_profile(vault=None):
    """Project the locked environment vault config without storing secrets."""
    vault = vault or ArtifactVault()
    identity = settings.ENV_IDENTITY
    endpoint = vault.config.endpoint or ""
    parsed = urlsplit(endpoint)
    endpoint_origin = (
        f"{parsed.scheme}://{parsed.netloc}"
        if parsed.scheme and parsed.netloc
        else ""
    )
    posture = {
        "endpoint_origin": endpoint_origin,
        "bucket": vault.config.bucket,
        "region": vault.config.region,
        "dataset_id": identity.dataset_id,
        "production_source_id": identity.production_source_id,
        "credential_alias": "environment:ARTIFACT_VAULT",
        "read_only": not identity.is_authoritative_writer,
    }
    fingerprint = hashlib.sha256(
        json.dumps(posture, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    profile, _ = VaultConnectionProfile.objects.update_or_create(
        key=settings.VAULT_DEFAULT_PROFILE,
        defaults={
            "display_name": "Locked environment vault",
            "source": VaultConnectionProfile.Source.ENVIRONMENT,
            "enabled": vault.enabled,
            "read_only": not identity.is_authoritative_writer,
            "environment_locked": True,
            "endpoint_origin": endpoint_origin,
            "bucket": vault.config.bucket,
            "region": vault.config.region,
            "dataset_id": identity.dataset_id,
            "production_source_id": identity.production_source_id,
            "credential_alias": "environment:ARTIFACT_VAULT",
            "fingerprint": fingerprint,
        },
    )
    return profile


def vault_for_profile(profile, *, expected_fingerprint="", resolver=None):
    """Build a server-side vault client after revalidating profile identity."""
    if not profile.enabled:
        raise VaultProfileError("vault_profile_disabled")
    current_fingerprint = profile_fingerprint(profile)
    if (
        not profile.fingerprint
        or profile.fingerprint != current_fingerprint
        or (
            expected_fingerprint
            and expected_fingerprint != current_fingerprint
        )
    ):
        raise VaultProfileError("profile_fingerprint_changed")
    validate_endpoint(profile.endpoint_origin, resolver=resolver)
    access_key, secret_key = resolve_credentials(profile.credential_alias)
    config = VaultConfig(
        enabled=True,
        endpoint=profile.endpoint_origin,
        bucket=profile.bucket,
        region=profile.region,
        access_key=access_key,
        secret_key=secret_key,
    )
    return ArtifactVault(config=config)


def upsert_restore_profile(
    *,
    key,
    display_name,
    endpoint_origin,
    bucket,
    region,
    dataset_id,
    production_source_id,
    credential_alias,
    resolver=None,
):
    """Create or update an approved, read-only profile without storing keys."""
    if not settings.VAULT_UI_PROFILE_CONFIGURATION_ENABLED:
        raise VaultProfileError("vault_profile_configuration_disabled")
    if (
        not ALIAS_RE.fullmatch(key or "")
        or not display_name
        or not bucket
        or not region
        or not dataset_id
        or not production_source_id
    ):
        raise VaultProfileError("vault_profile_invalid")
    evidence = validate_endpoint(endpoint_origin, resolver=resolver)
    if credential_alias == "environment:ARTIFACT_VAULT":
        raise VaultProfileError("environment_credential_alias_reserved")
    if credential_alias not in _credential_aliases():
        raise VaultProfileError("credential_alias_not_approved")
    with transaction.atomic(using="control"):
        existing = VaultConnectionProfile.objects.filter(key=key).first()
        if existing and existing.environment_locked:
            raise VaultProfileError("environment_profile_locked")
        profile, _ = VaultConnectionProfile.objects.update_or_create(
            key=key,
            defaults={
                "display_name": display_name,
                "source": VaultConnectionProfile.Source.STORED,
                "enabled": True,
                "read_only": True,
                "environment_locked": False,
                "endpoint_origin": evidence["origin"],
                "bucket": bucket,
                "region": region,
                "dataset_id": dataset_id,
                "production_source_id": production_source_id,
                "credential_alias": credential_alias,
                "capability_evidence": {},
                "last_probed_at": None,
            },
        )
        profile.fingerprint = profile_fingerprint(profile)
        profile.save(update_fields=["fingerprint", "updated_at"])
    return profile


def disable_profile(profile):
    if profile.environment_locked:
        raise VaultProfileError("environment_profile_locked")
    profile.enabled = False
    profile.save(update_fields=["enabled", "updated_at"])
    return profile


def probe_restore_profile(profile, *, resolver=None, vault=None):
    """Perform a least-privilege bucket HEAD and persist redacted evidence."""
    if vault is None:
        vault = vault_for_profile(
            profile,
            expected_fingerprint=profile.fingerprint,
            resolver=resolver,
        )
    else:
        validate_endpoint(profile.endpoint_origin, resolver=resolver)
        if profile.fingerprint != profile_fingerprint(profile):
            raise VaultProfileError("profile_fingerprint_changed")
    health = vault.health_check()
    evidence = {
        "configured": health.configured,
        "reachable": health.reachable,
        "bucket_exists": health.bucket_exists,
        "read_only_probe": True,
        "error_code": health.error_code or "",
    }
    profile.capability_evidence = evidence
    profile.last_probed_at = timezone.now()
    profile.save(
        update_fields=[
            "capability_evidence",
            "last_probed_at",
            "updated_at",
        ]
    )
    return evidence
