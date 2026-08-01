#!/usr/bin/env python3
"""Reject security-critical web/maintenance environment drift."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys


IMMUTABLE_IMAGE_PATTERN = re.compile(
    r"^(?P<repository>[^@\s]+)@sha256:[0-9a-f]{64}$"
)


CRITICAL_KEYS = {
    "APP_ENV",
    "APP_IMAGE_DIGEST",
    "APP_RELEASE_VERSION",
    "ARTIFACT_VAULT_ACCESS_KEY",
    "ARTIFACT_VAULT_BUCKET",
    "ARTIFACT_VAULT_ENABLED",
    "ARTIFACT_VAULT_ENDPOINT",
    "ARTIFACT_VAULT_REGION",
    "ARTIFACT_VAULT_SECRET_KEY",
    "DATAOPS_ENABLED",
    "DATAOPS_PROFILE_MANIFEST",
    "DATAOPS_ENV_PROFILES",
    "DATAOPS_AUTO_HEAL_ENABLED",
    "DATAOPS_BACKUP_PROFILE",
    "DATAOPS_RESTORE_PROFILE",
    "DATAOPS_BACKUP_MODE",
    "DATAOPS_BACKUP_INTERVAL_SECONDS",
    "DATAOPS_BACKUP_SOURCE_PROFILE",
    "DATAOPS_BACKUP_DESTINATION_PROFILE",
    "DATAOPS_RESTORE_SOURCE_PROFILE",
    "DATAOPS_RESTORE_DESTINATION_PROFILE",
    "DATAOPS_BACKUP_QUIET_PERIOD_SECONDS",
    "DATAOPS_AUTO_HEAL_MAX_RETRIES",
    "DATAOPS_OPERATION_LEASE_SECONDS",
    "DATAOPS_AUTO_HEAL_REINDEX_PER_RUN",
    "DATAOPS_AUTO_HEAL_REINDEX_PER_DAY",
    "DATAOPS_RESTORE_AUTO_ACTIVATE_STAGING",
    "DATAOPS_RESTORE_STAGING_ROOT",
    "DATAOPS_UI_CONFIG_ENABLED",
    "DATAOPS_UI_SECRET_ENTRY_ENABLED",
    "DATAOPS_CONFIG_ENCRYPTION_KEY",
    "ARTIFACT_INVENTORY_MAX_MEDIA_FILE_BYTES",
    "BACKUP_ROLE",
    "BACKUP_SYNC_MODE",
    "DEPLOYMENT_ID",
    "DATASET_ID",
    "AUTHORITATIVE_DATASET_ID",
    "PRODUCTION_SOURCE_ID",
    "DATA_MODE",
    "DATA_PINNED_GENERATION",
    "RESTORE_SOURCE_DATASET_ID",
    "RESTORE_POLICY",
    "MAINTENANCE_CANDIDATE_PREPARATION_ENABLED",
    "MAINTENANCE_CANDIDATE_WRITER_MODE",
    "MAINTENANCE_JOB_TIMEOUT_SECONDS",
    "MAINTENANCE_SCHEDULER_ENABLED",
    "MAINTENANCE_WORKER_HEARTBEAT_MAX_AGE_SECONDS",
    "MAINTENANCE_WORKER_HEARTBEAT_PATH",
    "MAINTENANCE_WORKER_READINESS_REQUIRED",
    "MAINTENANCE_WORKSPACE_ROOT",
    "NONPROD_DATA_POLICY",
    "VAULT_ADMIN_MUTATIONS_ENABLED",
    "VAULT_ALLOWED_S3_ENDPOINTS",
    "VAULT_CREDENTIAL_ALIASES",
    "VAULT_DEFAULT_PROFILE",
    "VAULT_JOB_HEARTBEAT_SECONDS",
    "VAULT_JOB_STALE_SECONDS",
    "VAULT_MUTATION_TRACKING_ENABLED",
    "VAULT_RESTORE_ENABLED",
    "VAULT_RESTORE_MIN_FREE_BYTES",
    "VAULT_RESTORE_MIN_FREE_INODES",
    "VAULT_RESTORE_REQUIRE_SANITIZATION",
    "VAULT_RESTORE_ALLOW_REPACKED_RELEASE_MISMATCH",
    "VAULT_SNAPSHOT_BARRIER_TIMEOUT_SECONDS",
    "VAULT_SNAPSHOT_CLEANUP_GRACE_SECONDS",
    "VAULT_SNAPSHOT_CLEANUP_MAX_ITEMS",
    "VAULT_SNAPSHOT_CLEANUP_MAX_SCAN_ITEMS",
    "VAULT_SNAPSHOT_CLEANUP_MAX_BYTES",
    "VAULT_SNAPSHOT_CLEANUP_MAX_SECONDS",
    "VAULT_SNAPSHOT_FAISS_MAX_PDF_JSON_BYTES",
    "VAULT_SNAPSHOT_ROOT",
    "VAULT_SYNC_ENABLED",
    "VAULT_SYNC_INTERVAL_SECONDS",
    "VAULT_SYNC_MAX_LAG_SECONDS",
    "VAULT_SYNC_MAX_PARALLEL_HASHERS",
    "VAULT_SYNC_MAX_PARALLEL_UPLOADS",
    "VAULT_SYNC_MODE",
    "VAULT_SYNC_PROMOTION_MODE",
    "VAULT_SYNC_QUIET_PERIOD_SECONDS",
    "VAULT_UI_PROFILE_CONFIGURATION_ENABLED",
    "VAULT_UI_SECRET_ENTRY_ENABLED",
    "VAULT_VALIDATION_MAX_AGE_SECONDS",
    "LOCAL_INDEX_MAINTENANCE_ENABLED",
    "FORCE_REINDEX_ENABLED",
    "EXTERNAL_EMBEDDINGS_ENABLED",
    "STAGING_RUNTIME_ACTIVATION_ENABLED",
    "STAGING_INITIAL_ACTIVATION_ENABLED",
    "STAGING_ACTIVATION_APPLY_MODE",
    "ACTIVATION_INTENT_SIGNING_KEY",
    "ACTIVATION_SMOKE_QUERIES_FILE",
    "ACTIVATION_RECOVERY_SUPERADMIN_USERNAME",
    "ACTIVATION_RECOVERY_SUPERADMIN_PASSWORD",
    "EXTERNAL_SIDE_EFFECTS_MODE",
}


def _is_canonical_immutable_image(reference):
    if not isinstance(reference, str):
        return False
    match = IMMUTABLE_IMAGE_PATTERN.fullmatch(reference)
    if match is None:
        return False
    # A colon in the final repository component denotes a mutable tag.
    # Colons in an earlier registry component remain valid host ports.
    repository_leaf = match.group("repository").rsplit("/", 1)[-1]
    return bool(repository_leaf) and ":" not in repository_leaf


def find_image_contract_errors(services):
    """Return bounded reason codes without disclosing image references."""
    errors = []
    references = {}
    for service_name in ("web", "maintenance"):
        service = services.get(service_name)
        if not isinstance(service, dict) or not service.get("image"):
            errors.append(f"{service_name}_image_missing")
            continue
        reference = service["image"]
        references[service_name] = reference
        if not _is_canonical_immutable_image(reference):
            errors.append(f"{service_name}_image_not_immutable")
    if (
        "web" in references
        and "maintenance" in references
        and references["web"] != references["maintenance"]
    ):
        errors.append("service_images_divergent")
    return sorted(errors)


def find_parity_errors(services):
    """Return key names only so validation never discloses configured values."""
    web = services["web"].get("environment", {})
    maintenance = services["maintenance"].get("environment", {})
    missing = sorted(
        key for key in CRITICAL_KEYS if key not in web or key not in maintenance
    )
    divergent = sorted(
        key
        for key in CRITICAL_KEYS
        if key in web and key in maintenance and web[key] != maintenance[key]
    )
    return missing, divergent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compose-file", default="docker-compose.yml")
    args = parser.parse_args()
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            args.compose_file,
            "config",
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    services = json.loads(result.stdout)["services"]
    missing, divergent = find_parity_errors(services)
    image_errors = find_image_contract_errors(services)
    if missing or divergent or image_errors:
        if missing:
            print("missing security-critical keys: " + ", ".join(missing))
        if divergent:
            print("divergent security-critical keys: " + ", ".join(divergent))
        if image_errors:
            print("invalid production image contract: " + ", ".join(image_errors))
        return 1
    print(
        "web/maintenance environment and immutable image parity verified "
        f"({len(CRITICAL_KEYS)} keys)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
