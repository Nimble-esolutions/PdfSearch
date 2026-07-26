import hashlib
import json
from urllib.parse import urlsplit

from django.conf import settings

from core.artifact_vault import ArtifactVault
from vaultops.models import VaultConnectionProfile


def materialize_environment_profile(vault=None):
    """Project the locked environment vault config without storing secrets."""
    vault = vault or ArtifactVault()
    identity = settings.ENV_IDENTITY
    endpoint = vault.config.endpoint or ""
    parsed = urlsplit(endpoint)
    endpoint_origin = (
        f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
    )
    posture = {
        "endpoint_origin": endpoint_origin,
        "bucket": vault.config.bucket,
        "region": vault.config.region,
        "dataset_id": identity.dataset_id,
        "production_source_id": identity.production_source_id,
        "credential_alias": "environment:ARTIFACT_VAULT",
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
