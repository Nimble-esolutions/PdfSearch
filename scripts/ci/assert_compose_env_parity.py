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
    "DATAOPS_CLONE_REBIND_ENABLED",
    "DATAOPS_PROFILE_MANIFEST",
    "DATAOPS_ENV_PROFILES",
    "DATAOPS_PROFILE_STAGE_2026_DISPLAY_NAME",
    "DATAOPS_PROFILE_STAGE_2026_ROLE",
    "DATAOPS_PROFILE_STAGE_2026_ENDPOINT",
    "DATAOPS_PROFILE_STAGE_2026_BUCKET",
    "DATAOPS_PROFILE_STAGE_2026_REGION",
    "DATAOPS_PROFILE_STAGE_2026_DATASET_ID",
    "DATAOPS_PROFILE_STAGE_2026_SOURCE_ID",
    "DATAOPS_PROFILE_STAGE_2026_NAMESPACE",
    "DATAOPS_PROFILE_STAGE_2026_PREFIX",
    "DATAOPS_PROFILE_STAGE_2026_CREDENTIAL_REF",
    "DATAOPS_PROFILE_STAGE_2026_ENABLED",
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
    "STAGE_PUBLIC_AUTH_EXCEPTION_REQUIRED",
    "STAGE_PUBLIC_AUTH_EXCEPTION_APPROVED",
    "STAGE_PUBLIC_AUTH_EXCEPTION_OWNER",
    "STAGE_PUBLIC_AUTH_EXCEPTION_MONITORING",
    "STAGE_PUBLIC_AUTH_EXCEPTION_INCIDENT_RESPONSE",
    "STAGE_PUBLIC_AUTH_EXCEPTION_ROLLBACK_AUTHORITY",
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


CORE_SERVICES = ("web", "maintenance", "redis")
APPLICATION_SERVICES = ("web", "maintenance")
SHARED_MOUNTS = ("/app/data", "/app/data-control")


def find_immutable_image_contract_errors(services):
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


# Backwards-compatible import used by older callers/tests.
find_image_contract_errors = find_immutable_image_contract_errors


def _mount_targets(service):
    targets = set()
    for mount in service.get("volumes", []) if isinstance(service, dict) else []:
        if isinstance(mount, str):
            parts = mount.split(":")
            if len(parts) > 1:
                targets.add(parts[1])
        elif isinstance(mount, dict) and mount.get("target"):
            targets.add(mount["target"])
    return targets


def _depends_condition(service, dependency):
    depends = service.get("depends_on", {}) if isinstance(service, dict) else {}
    value = depends.get(dependency) if isinstance(depends, dict) else None
    return value.get("condition") if isinstance(value, dict) else None


def find_topology_errors(services):
    errors = []
    for name in CORE_SERVICES:
        if not isinstance(services.get(name), dict):
            errors.append(f"{name}_service_missing")
    for name in APPLICATION_SERVICES:
        service = services.get(name, {})
        if not service.get("healthcheck"):
            errors.append(f"{name}_healthcheck_missing")
        if "internal" not in service.get("networks", {}):
            errors.append(f"{name}_internal_network_missing")
        for target in SHARED_MOUNTS:
            if target not in _mount_targets(service):
                errors.append(f"{name}_{target.rsplit('/', 1)[-1]}_mount_missing")
        if _depends_condition(service, "redis") != "service_healthy":
            errors.append(f"{name}_redis_healthy_dependency_missing")
    redis = services.get("redis", {})
    if isinstance(redis, dict) and not redis.get("healthcheck"):
        errors.append("redis_healthcheck_missing")
    return sorted(set(errors))


def _normalized_build(service):
    build = service.get("build") if isinstance(service, dict) else None
    if isinstance(build, str):
        return {"context": build}
    return build


def find_local_build_errors(services):
    errors = []
    images = {}
    builds = {}
    for name in APPLICATION_SERVICES:
        service = services.get(name, {})
        image = service.get("image")
        build = _normalized_build(service)
        if not image:
            errors.append(f"{name}_image_missing")
        else:
            images[name] = image
        if not build:
            errors.append(f"{name}_build_missing")
        else:
            builds[name] = build
    if len(images) == 2 and images["web"] != images["maintenance"]:
        errors.append("service_images_divergent")
    if len(builds) == 2 and builds["web"] != builds["maintenance"]:
        errors.append("service_builds_divergent")
    for name in APPLICATION_SERVICES:
        if services.get(name, {}).get("environment", {}).get("APP_IMAGE_DIGEST"):
            errors.append(f"{name}_local_digest_not_empty")
    return sorted(errors)


def find_release_image_errors(services):
    errors = find_immutable_image_contract_errors(services)
    references = {}
    for name in APPLICATION_SERVICES:
        service = services.get(name, {})
        if service.get("build"):
            errors.append(f"{name}_build_forbidden")
        if service.get("pull_policy") != "always":
            errors.append(f"{name}_pull_policy_not_always")
        if service.get("image"):
            references[name] = service["image"]
        if service.get("environment", {}).get("APP_IMAGE_DIGEST") != service.get("image"):
            errors.append(f"{name}_image_digest_mismatch")
    return sorted(set(errors))


def find_rustfs_errors(services):
    errors = []
    if not isinstance(services.get("rustfs"), dict):
        errors.append("rustfs_service_missing")
    init = services.get("rustfs-init")
    if not isinstance(init, dict):
        errors.append("rustfs_init_service_missing")
    else:
        if init.get("restart") not in ("no", "none", False):
            errors.append("rustfs_init_not_one_shot")
        if _depends_condition(init, "rustfs") not in ("service_started", "service_healthy"):
            errors.append("rustfs_init_dependency_missing")
    for name in APPLICATION_SERVICES:
        if _depends_condition(services.get(name, {}), "rustfs-init") != "service_completed_successfully":
            errors.append(f"{name}_rustfs_init_dependency_missing")
    return sorted(errors)


def find_contract_errors(services, deployment_mode="production"):
    errors = find_topology_errors(services)
    missing, divergent = find_parity_errors(services)
    errors.extend(f"environment_key_missing:{key}" for key in missing)
    errors.extend(f"environment_key_divergent:{key}" for key in divergent)
    if deployment_mode == "development":
        errors.extend(find_local_build_errors(services))
        errors.extend(find_rustfs_errors(services))
    else:
        errors.extend(find_release_image_errors(services))
    return sorted(set(errors))


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
    parser.add_argument(
        "--deployment-mode",
        choices=("development", "staging", "production"),
        default="production",
    )
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
    errors = find_contract_errors(services, args.deployment_mode)
    if errors:
        print("deployment_contract_invalid:" + ",".join(errors))
        return 1
    print(
        f"deployment_contract_verified:{args.deployment_mode}:"
        f"critical_keys={len(CRITICAL_KEYS)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
