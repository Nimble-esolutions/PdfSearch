"""Explicit environment identity and configuration validation.

Every running instance must declare its environment, dataset, and operational
role before accepting work. This module provides startup-time validation,
configuration-introspection, and safe defaults.

Corrections from Phase 1:
  - APP_ENV is explicit. Missing APP_ENV outside dev/test fails closed.
  - PRODUCTION_SOURCE_ID separates "who I am" from "what dataset I own."
  - RESTORE_SOURCE_DATASET_ID separates read source from local identity.
  - AUTHORITATIVE_DATASET_ID gates which dataset this env may write to.
  - INSTANCE_ID persists across container restarts via a stable file.
  - Build identity is derived from OCI labels, release file, or env.
"""

from __future__ import annotations

import enum
import json
import os
import secrets
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


class AppEnv(enum.Enum):
    PRODUCTION = "production"
    STAGING = "staging"
    DEVELOPMENT = "development"
    TEST = "test"
    REVIEW = "review"


class DataMode(enum.Enum):
    EMPTY = "empty"
    SEED = "seed"
    LOCAL = "local"
    S3_RESTORE = "s3-restore"
    S3_PINNED = "s3-pinned"
    SANITIZED_PRODUCTION = "sanitized-production"
    EXACT_PRODUCTION = "exact-production"


class BackupRole(enum.Enum):
    WRITER = "writer"
    READER = "reader"
    DISABLED = "disabled"


class BackupSyncMode(enum.Enum):
    MANUAL = "manual"
    SCHEDULED = "scheduled"
    EVENT_DRIVEN = "event-driven"
    HYBRID = "hybrid"


class RestorePolicy(enum.Enum):
    DISABLED = "disabled"
    MANUAL = "manual"
    STARTUP_LATEST = "startup-latest"
    STARTUP_PINNED = "startup-pinned"


class ExternalSideEffectsMode(enum.Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    SANDBOX = "sandbox"


INSTANCE_ID_FILE = "/app/data/.instance_id"


def _persisted_instance_id() -> str | None:
    """Read a stable INSTANCE_ID from the data volume if it exists."""
    path = Path(INSTANCE_ID_FILE)
    if path.is_file():
        try:
            raw = path.read_text().strip()
            if raw and len(raw) >= 8:
                return raw
        except Exception:
            pass
    return None


def _write_instance_id(instance_id: str) -> None:
    path = Path(INSTANCE_ID_FILE)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(instance_id)
    except Exception:
        pass


def _derive_build_identity() -> tuple[str, str]:
    """Derive immutable build identity from image labels, release file, or env.

    Returns (image_digest, release_version).
    Prefers OCI image labels embedded at build time over env-supplied values.
    """
    digest = ""
    release = ""

    for label_path in ("/app/.oci-labels.json", "/app/build-info.json"):
        try:
            raw = Path(label_path).read_text()
            data = json.loads(raw)
            if isinstance(data, dict):
                digest = data.get("image_digest", "") or data.get("oci_revision", "") or ""
                release = data.get("git_commit", "") or data.get("release_version", "") or ""
                if digest:
                    break
        except Exception:
            pass

    digest = digest or os.getenv("APP_IMAGE_DIGEST", "").strip()
    release = release or os.getenv("APP_RELEASE_VERSION", "").strip()

    if not digest:
        release_file = Path("/app/RELEASE.txt")
        if release_file.is_file():
            try:
                digest = release_file.read_text().strip().split("\n")[0].strip()
            except Exception:
                pass

    return digest, release


@dataclass(frozen=True)
class EnvironmentIdentity:
    """Immutable environment identity resolved once at startup."""

    app_env: AppEnv
    deployment_id: str = ""
    instance_id: str = ""
    replica_id: str = ""
    dataset_id: str = ""
    production_source_id: str = ""
    authoritative_dataset_id: str = ""
    restore_source_dataset_id: str = ""
    data_mode: DataMode = DataMode.LOCAL
    data_pinned_generation: str = ""
    restore_policy: RestorePolicy = RestorePolicy.DISABLED
    backup_role: BackupRole = BackupRole.DISABLED
    backup_sync_mode: BackupSyncMode = BackupSyncMode.MANUAL
    maintenance_scheduler_enabled: bool = False
    external_side_effects: ExternalSideEffectsMode = ExternalSideEffectsMode.ENABLED
    nonprod_data_policy: str = ""
    exact_production_data_approved: bool = False
    exact_production_approval_id: str = ""
    app_image_digest: str = ""
    app_release_version: str = ""
    build_image_digest: str = ""
    build_release_version: str = ""

    @property
    def is_production(self) -> bool:
        return self.app_env == AppEnv.PRODUCTION

    @property
    def is_backup_writer(self) -> bool:
        return self.backup_role == BackupRole.WRITER

    @property
    def external_effects_enabled(self) -> bool:
        return self.external_side_effects == ExternalSideEffectsMode.ENABLED

    @property
    def is_authoritative_writer(self) -> bool:
        return (
            self.is_backup_writer
            and self.is_production
            and self.dataset_id == self.authoritative_dataset_id
            and bool(self.production_source_id)
        )

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "EnvironmentIdentity":
        env = os.environ if environ is None else environ

        app_env = _parse_app_env(env.get("APP_ENV", ""))
        dataset_id = _validate_dataset_id(env.get("DATASET_ID", "").strip())
        production_source_id = env.get("PRODUCTION_SOURCE_ID", "").strip()
        authoritative_dataset_id = _validate_dataset_id(
            env.get("AUTHORITATIVE_DATASET_ID", "").strip() or dataset_id
        )
        restore_source_dataset_id = env.get("RESTORE_SOURCE_DATASET_ID", "").strip()

        deployment_id = env.get("DEPLOYMENT_ID", "").strip()
        instance_id = _resolve_instance_id(env.get("INSTANCE_ID", "").strip())
        replica_id = env.get("REPLICA_ID", "") or _default_replica_id()

        data_mode = _parse_data_mode(env.get("DATA_MODE", ""), app_env)
        data_pinned_generation = ""
        if data_mode == DataMode.S3_PINNED:
            data_pinned_generation = env.get("DATA_PINNED_GENERATION", "").strip()

        restore_policy = _parse_restore_policy(env.get("RESTORE_POLICY", ""), data_mode)

        backup_role = _parse_backup_role(env.get("BACKUP_ROLE", ""), app_env)
        backup_sync_mode = _parse_backup_sync_mode(env.get("BACKUP_SYNC_MODE", ""))
        maintenance_scheduler = env.get(
            "MAINTENANCE_SCHEDULER_ENABLED", ""
        ).strip().lower() in {"1", "true", "yes"}

        external_side_effects = _parse_side_effects_mode(
            env.get("EXTERNAL_SIDE_EFFECTS_MODE", ""), app_env
        )

        nonprod_data_policy = env.get("NONPROD_DATA_POLICY", "").strip()
        exact_approved = env.get(
            "EXACT_PRODUCTION_DATA_APPROVED", "0"
        ).strip().lower() in {"1", "true", "yes"}
        exact_approval_id = env.get(
            "EXACT_PRODUCTION_DATA_APPROVAL_ID", ""
        ).strip()

        env_digest = env.get("APP_IMAGE_DIGEST", "").strip()
        env_release = env.get("APP_RELEASE_VERSION", "").strip()
        build_digest, build_release = _derive_build_identity()
        app_digest = env_digest or build_digest
        app_release = env_release or build_release

        return cls(
            app_env=app_env,
            deployment_id=deployment_id,
            instance_id=instance_id,
            replica_id=replica_id,
            dataset_id=dataset_id,
            production_source_id=production_source_id,
            authoritative_dataset_id=authoritative_dataset_id,
            restore_source_dataset_id=restore_source_dataset_id,
            data_mode=data_mode,
            data_pinned_generation=data_pinned_generation,
            restore_policy=restore_policy,
            backup_role=backup_role,
            backup_sync_mode=backup_sync_mode,
            maintenance_scheduler_enabled=maintenance_scheduler,
            external_side_effects=external_side_effects,
            nonprod_data_policy=nonprod_data_policy,
            exact_production_data_approved=exact_approved,
            exact_production_approval_id=exact_approval_id,
            app_image_digest=app_digest,
            app_release_version=app_release,
            build_image_digest=build_digest,
            build_release_version=build_release,
        )

    def validate(self) -> list[str]:
        """Return a list of configuration errors. Empty list means valid."""
        errors: list[str] = []

        if self.is_production:
            if not self.deployment_id:
                errors.append("DEPLOYMENT_ID is required in production")
            if not self.dataset_id:
                errors.append("DATASET_ID is required in production")
            if not self.production_source_id:
                errors.append("PRODUCTION_SOURCE_ID is required in production")
            if not self.authoritative_dataset_id:
                errors.append("AUTHORITATIVE_DATASET_ID is required in production")
            if self.dataset_id != self.authoritative_dataset_id and self.is_backup_writer:
                errors.append(
                    f"DATASET_ID ({self.dataset_id}) must equal "
                    f"AUTHORITATIVE_DATASET_ID ({self.authoritative_dataset_id}) "
                    f"when BACKUP_ROLE=writer"
                )

        if self.is_backup_writer and not self.production_source_id:
            errors.append("PRODUCTION_SOURCE_ID is required when BACKUP_ROLE=writer")

        if self.is_production and self.data_mode in (
            DataMode.EMPTY, DataMode.SEED, DataMode.EXACT_PRODUCTION,
            DataMode.SANITIZED_PRODUCTION,
        ):
            errors.append(
                f"DATA_MODE={self.data_mode.value} is not allowed in production"
            )

        if self.data_mode == DataMode.EXACT_PRODUCTION:
            if self.is_production:
                errors.append("exact-production data mode is not allowed in production")
            if self.nonprod_data_policy != "exact":
                errors.append("NONPROD_DATA_POLICY must be 'exact' for exact-production")
            if not self.exact_production_data_approved:
                errors.append(
                    "EXACT_PRODUCTION_DATA_APPROVED=1 is required for exact-production data mode"
                )

        if self.data_mode in (DataMode.S3_RESTORE, DataMode.S3_PINNED, DataMode.SANITIZED_PRODUCTION):
            if not self.restore_source_dataset_id:
                errors.append(
                    f"RESTORE_SOURCE_DATASET_ID is required when DATA_MODE={self.data_mode.value}"
                )
            if self.restore_source_dataset_id == self.dataset_id and not self.is_production:
                errors.append(
                    "RESTORE_SOURCE_DATASET_ID must differ from DATASET_ID in non-production "
                    "to prevent accidental writes to the production namespace"
                )

        if self.data_mode == DataMode.S3_PINNED and not self.data_pinned_generation:
            errors.append("DATA_PINNED_GENERATION is required when DATA_MODE=s3-pinned")

        if self.is_backup_writer:
            vault_enabled = _env_bool("ARTIFACT_VAULT_ENABLED")
            if not vault_enabled:
                errors.append("ARTIFACT_VAULT_ENABLED must be set when BACKUP_ROLE=writer")
            missing_vault = _missing_vault_config()
            if missing_vault:
                errors.append(
                    "S3 vault configuration incomplete when BACKUP_ROLE=writer: "
                    + ", ".join(missing_vault)
                )

        if self.is_production and not self.external_effects_enabled:
            errors.append("EXTERNAL_SIDE_EFFECTS_MODE must be 'enabled' in production")

        if (not self.is_production
                and self.external_effects_enabled
                and self.data_mode != DataMode.LOCAL
                and self.data_mode != DataMode.EMPTY
                and self.data_mode != DataMode.SEED):
            errors.append(
                "External side effects must be disabled or sandboxed in "
                "non-production environments using production-derived data"
            )

        if self.data_mode in (DataMode.S3_RESTORE, DataMode.S3_PINNED, DataMode.SANITIZED_PRODUCTION) \
                and self.is_backup_writer:
            errors.append(
                "Cannot restore from S3 while configured as a backup writer; "
                "set BACKUP_ROLE to 'reader' or 'disabled'"
            )

        staging_as_writer = (
            not self.is_production
            and self.is_backup_writer
            and self.authoritative_dataset_id
        )
        if staging_as_writer:
            errors.append(
                f"BACKUP_ROLE=writer is not allowed in {self.app_env.value}; "
                f"only production may be an authoritative writer"
            )

        if self.build_image_digest and self.app_image_digest and \
                self.build_image_digest != self.app_image_digest:
            errors.append(
                f"Build image digest ({self.build_image_digest[:19]}...) "
                f"does not match APP_IMAGE_DIGEST ({self.app_image_digest[:19]}...)"
            )

        return errors


def _resolve_instance_id(env_value: str) -> str:
    """Resolve stable INSTANCE_ID: persisted file > env var > generated + persisted."""
    if env_value:
        _write_instance_id(env_value)
        return env_value

    persisted = _persisted_instance_id()
    if persisted:
        return persisted

    hostname = "unknown"
    try:
        hostname = socket.gethostname()
    except Exception:
        pass
    generated = f"{hostname}-{secrets.token_hex(6)}"
    _write_instance_id(generated)
    return generated


def _validate_dataset_id(value: str) -> str:
    """Validate dataset ID format. Rejects unsafe characters."""
    if not value:
        return ""
    if "/" in value or ".." in value or "\\" in value:
        raise EnvironmentIdentityError(
            f"Dataset ID contains unsafe characters: {value}"
        )
    if len(value) > 100:
        raise EnvironmentIdentityError(
            f"Dataset ID exceeds maximum length of 100: {value}"
        )
    for char in value:
        if ord(char) < 32 or ord(char) > 126:
            raise EnvironmentIdentityError(
                f"Dataset ID contains non-printable characters: {value}"
            )
    return value


def _parse_app_env(value: str) -> AppEnv:
    value = value.strip().lower()
    mapping = {
        "production": AppEnv.PRODUCTION, "prod": AppEnv.PRODUCTION,
        "staging": AppEnv.STAGING, "stage": AppEnv.STAGING,
        "development": AppEnv.DEVELOPMENT, "dev": AppEnv.DEVELOPMENT,
        "test": AppEnv.TEST,
        "review": AppEnv.REVIEW,
    }
    if value in mapping:
        return mapping[value]
    if value:
        raise EnvironmentIdentityError(f"Unknown APP_ENV: {value}")

    allow_unsafe = _env_bool("ALLOW_INSECURE_DEFAULTS")
    in_test = "PYTEST_CURRENT_TEST" in os.environ or "pytest" in os.environ.get("_", "")
    if allow_unsafe or in_test:
        return AppEnv.DEVELOPMENT

    raise EnvironmentIdentityError(
        "APP_ENV is required. Set APP_ENV=production|staging|development|test|review. "
        "Use ALLOW_INSECURE_DEFAULTS=1 only for local development."
    )


def _parse_data_mode(value: str, app_env: AppEnv) -> DataMode:
    value = value.strip().lower()
    mapping = {
        "empty": DataMode.EMPTY,
        "seed": DataMode.SEED,
        "local": DataMode.LOCAL,
        "s3-restore": DataMode.S3_RESTORE,
        "s3-pinned": DataMode.S3_PINNED,
        "sanitized-production": DataMode.SANITIZED_PRODUCTION,
        "exact-production": DataMode.EXACT_PRODUCTION,
    }
    return mapping.get(value, DataMode.LOCAL)


def _parse_backup_role(value: str, app_env: AppEnv) -> BackupRole:
    value = value.strip().lower()
    mapping = {
        "writer": BackupRole.WRITER,
        "reader": BackupRole.READER,
        "disabled": BackupRole.DISABLED,
    }
    if value in mapping:
        return mapping[value]
    if not value:
        return BackupRole.DISABLED
    return BackupRole.DISABLED


def _parse_backup_sync_mode(value: str) -> BackupSyncMode:
    value = value.strip().lower()
    mapping = {
        "manual": BackupSyncMode.MANUAL,
        "scheduled": BackupSyncMode.SCHEDULED,
        "event-driven": BackupSyncMode.EVENT_DRIVEN,
        "hybrid": BackupSyncMode.HYBRID,
    }
    return mapping.get(value, BackupSyncMode.MANUAL)


def _parse_restore_policy(value: str, data_mode: DataMode) -> RestorePolicy:
    value = value.strip().lower()
    mapping = {
        "disabled": RestorePolicy.DISABLED,
        "manual": RestorePolicy.MANUAL,
        "startup-latest": RestorePolicy.STARTUP_LATEST,
        "startup-pinned": RestorePolicy.STARTUP_PINNED,
    }
    if value in mapping:
        return mapping[value]
    if data_mode in (DataMode.S3_RESTORE, DataMode.S3_PINNED, DataMode.SANITIZED_PRODUCTION):
        return RestorePolicy.STARTUP_LATEST if data_mode == DataMode.S3_RESTORE else RestorePolicy.STARTUP_PINNED
    return RestorePolicy.DISABLED


def _parse_side_effects_mode(value: str, app_env: AppEnv) -> ExternalSideEffectsMode:
    value = value.strip().lower()
    mapping = {
        "enabled": ExternalSideEffectsMode.ENABLED,
        "disabled": ExternalSideEffectsMode.DISABLED,
        "sandbox": ExternalSideEffectsMode.SANDBOX,
    }
    if value in mapping:
        return mapping[value]
    if not value:
        if app_env == AppEnv.PRODUCTION:
            return ExternalSideEffectsMode.ENABLED
        return ExternalSideEffectsMode.DISABLED
    return ExternalSideEffectsMode.DISABLED


def _default_replica_id() -> str:
    pid = os.getpid()
    rand = secrets.token_hex(4)
    return f"replica-{pid}-{rand}"


def _env_bool(name: str) -> bool:
    raw = os.getenv(name, "0")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _missing_vault_config() -> list[str]:
    missing = []
    for name in (
        "ARTIFACT_VAULT_ENDPOINT", "ARTIFACT_VAULT_BUCKET",
        "ARTIFACT_VAULT_REGION", "ARTIFACT_VAULT_ACCESS_KEY",
        "ARTIFACT_VAULT_SECRET_KEY",
    ):
        if not os.getenv(name, "").strip():
            missing.append(name)
    return missing


class EnvironmentIdentityError(ValueError):
    """Raised when the environment identity is invalid or contradictory."""
