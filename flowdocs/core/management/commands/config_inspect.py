"""Print effective non-secret environment configuration.

Usage: python manage.py config_inspect

Prints every environment-driven decision and why each feature is enabled
or disabled. Never prints secret values.
"""

from __future__ import annotations

import os
import sys

from django.core.management.base import BaseCommand

from core.environment import EnvironmentIdentity
from core.side_effects import SideEffectPolicy


class Command(BaseCommand):
    help = "Print effective non-secret environment configuration"

    def handle(self, *args, **options):
        identity = EnvironmentIdentity.from_env()
        errors = identity.validate()
        policy = SideEffectPolicy.from_identity(identity)

        self._section("Environment Identity")
        self._kv("APP_ENV", identity.app_env.value)
        self._kv("DEPLOYMENT_ID", identity.deployment_id or "(not set)")
        self._kv("INSTANCE_ID", identity.instance_id)
        self._kv("REPLICA_ID", identity.replica_id)
        self._kv("DATASET_ID", identity.dataset_id or "(not set)")
        self._kv("DATA_MODE", identity.data_mode.value)
        if identity.data_pinned_generation:
            self._kv("DATA_PINNED_GENERATION", identity.data_pinned_generation)
        self._kv("APP_IMAGE_DIGEST", identity.app_image_digest or "(not set)")
        self._kv("APP_RELEASE_VERSION", identity.app_release_version or "(not set)")

        self._section("Backup Configuration")
        self._kv("BACKUP_ROLE", identity.backup_role.value)
        self._kv("BACKUP_SYNC_MODE", identity.backup_sync_mode.value)
        self._kv("ARTIFACT_VAULT_ENABLED", _env_bool_str("ARTIFACT_VAULT_ENABLED"))
        if _env_bool_str("ARTIFACT_VAULT_ENABLED") == "yes":
            self._kv("ARTIFACT_VAULT_ENDPOINT", _env_str("ARTIFACT_VAULT_ENDPOINT"))
            self._kv("ARTIFACT_VAULT_BUCKET", _env_str("ARTIFACT_VAULT_BUCKET"))
            self._kv("ARTIFACT_VAULT_REGION", _env_str("ARTIFACT_VAULT_REGION"))
            self._kv("ARTIFACT_VAULT_ACCESS_KEY", "***" if _env_str("ARTIFACT_VAULT_ACCESS_KEY") else "(not set)")
            self._kv("ARTIFACT_VAULT_SECRET_KEY", "***" if _env_str("ARTIFACT_VAULT_SECRET_KEY") else "(not set)")

        self._section("External Side Effects")
        self._kv("EXTERNAL_SIDE_EFFECTS_MODE", identity.external_side_effects.value)
        self._kv("Email enabled", "yes" if policy.email_enabled else "no")
        self._kv("Email backend", policy.email_backend)
        self._kv("Payment enabled", "yes" if policy.payment_enabled else "no")
        self._kv("Webhook enabled", "yes" if policy.webhook_enabled else "no")
        self._kv("SMS enabled", "yes" if policy.sms_enabled else "no")
        self._kv("Analytics enabled", "yes" if policy.analytics_enabled else "no")
        self._kv("Search indexers allowed", "yes" if policy.indexers_allowed else "no")
        self._kv("Notifications enabled", "yes" if policy.notification_enabled else "no")

        self._section("Non-Production Data Policy")
        self._kv("NONPROD_DATA_POLICY", identity.nonprod_data_policy or "(not set)")

        self._section("Data Paths")
        self._kv("DATA_ROOT", _env_str("DATA_ROOT"))
        self._kv("SQLITE_DB_PATH", _env_str("SQLITE_DB_PATH"))
        self._kv("MEDIA_ROOT", _env_str("MEDIA_ROOT"))
        self._kv("FAISS_INDEX_DIR", _env_str("FAISS_INDEX_DIR"))
        self._kv("CHROMA_DIR", _env_str("CHROMA_DIR"))
        self._kv("BACKUP_DIR", _env_str("BACKUP_DIR"))

        self._section("Search Configuration")
        self._kv("PUBLIC_SEARCH_ENABLED", _env_bool_str("PUBLIC_SEARCH_ENABLED"))
        self._kv("OPENAI_EMBED_MODEL", _env_str("OPENAI_EMBED_MODEL"))
        self._kv("OPENAI_CHAT_MODEL", _env_str("OPENAI_CHAT_MODEL"))
        self._kv("REDIS_URL", "configured" if _env_str("REDIS_URL") else "(not set)")
        self._kv("DEBUG", _env_bool_str("DEBUG"))

        self._section("Validation Results")
        if errors:
            self.stderr.write(self.style.ERROR(f"  {len(errors)} configuration error(s):"))
            for error in errors:
                self.stderr.write(self.style.ERROR(f"    - {error}"))
            sys.exit(1)
        else:
            self.stdout.write(self.style.SUCCESS("  Configuration is valid."))

    def _section(self, title: str) -> None:
        self.stdout.write(f"\n{self.style.MIGRATE_HEADING(title)}")

    def _kv(self, key: str, value: str) -> None:
        self.stdout.write(f"  {key}: {value}")


def _env_str(name: str) -> str:
    return os.getenv(name, "").strip()


def _env_bool_str(name: str) -> str:
    raw = os.getenv(name, "0").strip()
    return "yes" if raw.lower() in {"1", "true", "yes", "on"} else "no"
