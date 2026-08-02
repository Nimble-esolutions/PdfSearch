"""Profile manifest and operation-selection policy for Data Operations.

The manifest is deliberately boring to consume: the web process and the
maintenance process receive the same JSON value and resolve it into the same
immutable profile objects.  The older comma-separated/profile-key variables
remain supported while deployments migrate to ``DATAOPS_PROFILE_MANIFEST``.

Only non-secret configuration is returned by this module.  Credential values
are resolved by :mod:`dataops.credentials` at execution time and never enter
the UI state, operation receipt, or manifest.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from django.core.exceptions import ImproperlyConfigured


_ID_RE = re.compile(r"[^A-Z0-9]+")
_SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
_SAFE_NAMESPACE_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,199}$")
_BOOLS = {"1": True, "true": True, "yes": True, "on": True, "0": False, "false": False, "no": False, "off": False}
ROLES = frozenset({"backup", "restore", "both"})
SOURCES = frozenset({"environment", "stored", "default"})


def _env_bool(name: str, default: bool = False, environ: Mapping[str, str] | None = None) -> bool:
    value = (os.environ if environ is None else environ).get(name)
    if value is None:
        return default
    try:
        return _BOOLS[value.strip().lower()]
    except KeyError as exc:
        raise ImproperlyConfigured(f"{name} must be a boolean") from exc


def _profile_token(key: str) -> str:
    token = _ID_RE.sub("_", key.upper()).strip("_")
    if not token:
        raise ImproperlyConfigured("DATAOPS_ENV_PROFILES contains an empty profile id")
    return token


def _profile_key(raw: object) -> str:
    value = str(raw or "").strip().lower().replace(" ", "-")
    if not _SAFE_ID_RE.fullmatch(value):
        raise ImproperlyConfigured("profile name must contain only lowercase letters, numbers, _ or -")
    return value


def _namespace(raw: object, *, fallback: str) -> str:
    value = str(raw or "").strip().strip("/").lower()
    if not value:
        value = fallback
    parts = value.split("/")
    if not _SAFE_NAMESPACE_RE.fullmatch(value) or any(part in {"", ".", ".."} for part in parts):
        raise ImproperlyConfigured("profile namespace is invalid")
    return value


def _as_bool(value: object, *, field: str, default: bool = True) -> bool:
    if value is None or str(value).strip() == "":
        return default
    normalized = str(value).strip().lower()
    if normalized in _BOOLS:
        return _BOOLS[normalized]
    raise ImproperlyConfigured(f"{field} must be a boolean")


def _manifest_value(raw: object) -> object:
    """Decode a structured manifest, preserving a useful error code."""
    if isinstance(raw, (list, tuple, dict)):
        return raw
    text = str(raw or "").strip()
    if not text:
        return None
    if text[0] not in "[{":
        return text
    try:
        return json.loads(text)
    except (TypeError, ValueError) as exc:
        raise ImproperlyConfigured("DATAOPS_PROFILE_MANIFEST is invalid JSON") from exc


def _manifest_entries(raw: object) -> tuple[Mapping[str, Any], ...]:
    """Normalize JSON list/map forms and the legacy CSV form."""
    decoded = _manifest_value(raw)
    if decoded is None:
        return ()
    if isinstance(decoded, str):
        return tuple({"name": item.strip()} for item in decoded.split(",") if item.strip())
    if isinstance(decoded, list):
        entries = decoded
    elif isinstance(decoded, dict):
        entries = decoded.get("profiles", decoded)
        if isinstance(entries, dict):
            entries = [{"name": name, **(value if isinstance(value, dict) else {})} for name, value in entries.items()]
    else:
        raise ImproperlyConfigured("DATAOPS_PROFILE_MANIFEST must be a list or object")
    if not isinstance(entries, list) or any(not isinstance(item, Mapping) for item in entries):
        raise ImproperlyConfigured("DATAOPS_PROFILE_MANIFEST profiles must be objects")
    return tuple(entries)


def _stored_profile_entries(stored_profiles: Iterable[object] | None) -> tuple[Mapping[str, Any], ...]:
    if stored_profiles is None:
        return ()
    entries: list[Mapping[str, Any]] = []
    for profile in stored_profiles:
        if isinstance(profile, Mapping):
            entries.append(profile)
            continue
        entries.append(
            {
                "name": getattr(profile, "key", ""),
                "display_name": getattr(profile, "display_name", ""),
                "role": getattr(profile, "role", "both"),
                "endpoint": getattr(profile, "endpoint", ""),
                "bucket": getattr(profile, "bucket", ""),
                "region": getattr(profile, "region", ""),
                "dataset_id": getattr(profile, "dataset_id", ""),
                "source_id": getattr(profile, "source_id", ""),
                "namespace": getattr(profile, "namespace", "") or getattr(profile, "prefix", ""),
                "credential_ref": getattr(profile, "credential_ref", "") or getattr(profile, "credential_prefix", ""),
                "enabled": getattr(profile, "enabled", True),
                "source": getattr(profile, "source", "stored"),
            }
        )
    return tuple(entries)


@dataclass(frozen=True)
class ResolvedProfile:
    key: str
    display_name: str
    role: str
    endpoint: str
    bucket: str
    region: str
    dataset_id: str
    source_id: str
    credential_prefix: str
    namespace: str = ""
    enabled: bool = True
    effective_source: str = "environment"
    environment_locked: bool = True

    @property
    def name(self) -> str:
        return self.key

    @property
    def prefix(self) -> str:
        return self.namespace

    @property
    def credential_ref(self) -> str:
        return self.credential_prefix

    @property
    def can_backup(self) -> bool:
        return self.enabled and self.role in {"backup", "both"}

    @property
    def can_restore(self) -> bool:
        return self.enabled and self.role in {"restore", "both"}

    @property
    def fingerprint(self) -> str:
        payload = "|".join(
            (
                self.key,
                self.endpoint,
                self.bucket,
                self.region,
                self.dataset_id,
                self.source_id,
                self.namespace,
                self.credential_prefix,
                str(self.enabled),
            )
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def redacted(self) -> dict[str, str | bool]:
        return {
            "key": self.key,
            "display_name": self.display_name,
            "role": self.role,
            "endpoint": self.endpoint,
            "bucket": self.bucket,
            "region": self.region,
            "dataset_id": self.dataset_id,
            "source_id": self.source_id,
            "credential_prefix": self.credential_prefix,
            "credential_ref": self.credential_ref,
            "namespace": self.namespace,
            "prefix": self.prefix,
            "enabled": self.enabled,
            "effective_source": self.effective_source,
            "environment_locked": self.environment_locked,
            "fingerprint": self.fingerprint,
        }


def _profile_from_entry(entry: Mapping[str, Any], env: Mapping[str, str], *, source: str) -> ResolvedProfile:
    raw_key = entry.get("name", entry.get("key", ""))
    key = _profile_key(raw_key)
    token = _profile_token(key)
    prefix = f"DATAOPS_PROFILE_{token}_"

    def value(field: str, *aliases: str, default: str = "") -> str:
        for name in (field, *aliases):
            if name in entry and str(entry[name]).strip() != "":
                return str(entry[name]).strip()
        for name in (prefix + field.upper(), *(prefix + alias.upper() for alias in aliases)):
            if env.get(name, "").strip() != "":
                return env[name].strip()
        return default

    role = value("role", default="both").lower()
    if role not in ROLES:
        raise ImproperlyConfigured(f"{prefix}ROLE must be backup, restore or both")
    bucket = value("bucket")
    dataset_id = value("dataset", "dataset_id")
    if not bucket:
        raise ImproperlyConfigured(f"{prefix}BUCKET is required")
    if not dataset_id:
        raise ImproperlyConfigured(f"{prefix}DATASET_ID is required")
    namespace = _namespace(value("namespace", "prefix"), fallback=key)
    credential = value("credential_ref", "credential_prefix")
    enabled = _as_bool(entry.get("enabled", env.get(prefix + "ENABLED")), field=f"{prefix}ENABLED")
    return ResolvedProfile(
        key=key,
        display_name=value("display_name", default=key.replace("_", " ").title()),
        role=role,
        endpoint=value("endpoint"),
        bucket=bucket,
        region=value("region"),
        dataset_id=dataset_id,
        source_id=value("source_id", "source"),
        credential_prefix=credential,
        namespace=namespace,
        enabled=enabled,
        effective_source=source if source in SOURCES else "default",
        environment_locked=source == "environment",
    )


def _stored_profiles_from_database() -> tuple[object, ...]:
    try:
        from django.apps import apps

        if not apps.ready:
            return ()
        model = apps.get_model("dataops", "DataProfile")
        return tuple(model.objects.using("control").filter(enabled=True))
    except Exception:
        # Configuration pages must render a typed issue when the disposable
        # control DB is unavailable; importing settings must remain possible.
        return ()


def resolve_profiles(
    environ: Mapping[str, str] | None = None,
    *,
    stored_profiles: Iterable[object] | None = None,
    include_stored: bool = True,
) -> tuple[ResolvedProfile, ...]:
    """Resolve one canonical profile set with ENV > encrypted DB fallback.

    ``environ`` is accepted for deterministic tests.  When it is supplied,
    database lookup is intentionally skipped unless ``stored_profiles`` is
    explicitly passed.
    """
    env = os.environ if environ is None else environ
    raw = env.get("DATAOPS_PROFILE_MANIFEST", "").strip() or env.get("DATAOPS_ENV_PROFILES", "").strip()
    source = "environment" if raw else "stored"
    entries = _manifest_entries(raw) if raw else _stored_profile_entries(
        stored_profiles if stored_profiles is not None else (_stored_profiles_from_database() if include_stored and environ is None else ())
    )
    if not entries:
        # Compatibility for the single ARTIFACT_VAULT_* deployment contract.
        legacy_bucket = env.get("ARTIFACT_VAULT_BUCKET", "").strip()
        if legacy_bucket:
            entries = ({"name": env.get("DATAOPS_BACKUP_PROFILE", "production"), "role": "both", "bucket": legacy_bucket, "dataset_id": env.get("DATASET_ID", "flowdocs-prod"), "endpoint": env.get("ARTIFACT_VAULT_ENDPOINT", ""), "region": env.get("ARTIFACT_VAULT_REGION", ""), "source_id": env.get("PRODUCTION_SOURCE_ID", ""), "credential_ref": "ARTIFACT_VAULT", "namespace": "legacy"},)
            source = "environment"
    profiles = tuple(_profile_from_entry(entry, env, source=source) for entry in entries)
    keys = [profile.key for profile in profiles]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise ImproperlyConfigured("duplicate profile names: " + ", ".join(duplicates))
    return profiles


def profile_map(profiles: Iterable[ResolvedProfile]) -> dict[str, ResolvedProfile]:
    return {profile.key: profile for profile in profiles}


def resolve_selectors(environ: Mapping[str, str] | None = None, *, stored: Mapping[str, object] | None = None) -> dict[str, str]:
    """Return explicit direction selectors and optional per-operation overrides."""
    env = os.environ if environ is None else environ
    stored = stored or {}

    def pick(name: str) -> str:
        value, _ = resolve_setting(name, stored.get(name), "", environ=env)
        return str(value or "").strip().lower()

    return {
        "backup": pick("DATAOPS_BACKUP_PROFILE"),
        "restore": pick("DATAOPS_RESTORE_PROFILE"),
        "backup_source": pick("DATAOPS_BACKUP_SOURCE_PROFILE"),
        "backup_destination": pick("DATAOPS_BACKUP_DESTINATION_PROFILE"),
        "restore_source": pick("DATAOPS_RESTORE_SOURCE_PROFILE"),
        "restore_destination": pick("DATAOPS_RESTORE_DESTINATION_PROFILE"),
    }


@dataclass(frozen=True)
class ConfigurationIssue:
    code: str
    message: str
    field: str = ""
    severity: str = "error"


def profile_for_operation(profiles: Iterable[ResolvedProfile], operation: str, selector: str = "") -> ResolvedProfile | None:
    selected = selector.strip().lower()
    values = profile_map(profiles)
    if not selected:
        return None
    return values.get(selected)


def validate_profiles(
    profiles: Iterable[ResolvedProfile],
    *,
    selectors: Mapping[str, str] | None = None,
    operation: str | None = None,
    source: ResolvedProfile | None = None,
    destination: ResolvedProfile | None = None,
    local_dataset_id: str = "",
    credential_environment: Mapping[str, str] | None = None,
) -> tuple[ConfigurationIssue, ...]:
    """Validate configuration without making a network request.

    Network permission checks are intentionally separate and live in
    ``storage.probe_profile_access`` so a UI preview can distinguish a bad
    manifest from an unavailable object store.
    """
    profiles = tuple(profiles)
    selectors = selectors or {}
    if credential_environment is None:
        credential_environment = os.environ
    issues: list[ConfigurationIssue] = []
    seen: set[str] = set()
    for profile in profiles:
        if profile.key in seen:
            issues.append(ConfigurationIssue("duplicate_profile_name", f"Profile name {profile.key} is used more than once", "name"))
        seen.add(profile.key)
        if profile.role not in ROLES:
            issues.append(ConfigurationIssue("invalid_profile_role", f"Profile {profile.key} has an unsupported role", "role"))
        if profile.enabled and not profile.bucket:
            issues.append(ConfigurationIssue("profile_bucket_missing", f"Profile {profile.key} has no bucket", "bucket"))
        if profile.enabled and not profile.dataset_id:
            issues.append(ConfigurationIssue("profile_dataset_missing", f"Profile {profile.key} has no dataset", "dataset_id"))
        if profile.enabled and not profile.credential_prefix:
            issues.append(ConfigurationIssue("credential_reference_missing", f"Profile {profile.key} has no credential reference", "credential_ref"))
        if profile.credential_prefix:
            prefix = profile.credential_prefix
            access = str(credential_environment.get(f"{prefix}_ACCESS_KEY", "") or "").strip()
            secret = str(credential_environment.get(f"{prefix}_SECRET_KEY", "") or "").strip()
            if bool(access) != bool(secret):
                issues.append(ConfigurationIssue("credential_reference_mismatch", f"Credential reference for {profile.key} is incomplete", "credential_ref"))
    for index, left in enumerate(profiles):
        for right in profiles[index + 1:]:
            same_store = (
                left.endpoint.rstrip("/").lower(),
                left.bucket,
                left.dataset_id,
            ) == (
                right.endpoint.rstrip("/").lower(),
                right.bucket,
                right.dataset_id,
            )
            if same_store and left.namespace == right.namespace:
                issues.append(ConfigurationIssue("same_bucket_namespace_collision", f"Profiles {left.key} and {right.key} share an object-store namespace", "namespace"))
    if operation in {"backup", "restore"} and not profiles:
        issues.append(ConfigurationIssue("profile_selector_missing", f"No {operation} profile is configured", f"{operation}_profile"))
    if operation == "backup":
        selected = selectors.get("backup_destination") or selectors.get("backup")
        if not selected:
            issues.append(ConfigurationIssue("profile_selector_missing", "Backup profile selector is missing", "DATAOPS_BACKUP_PROFILE"))
        elif not profile_for_operation(profiles, operation, selected):
            issues.append(ConfigurationIssue("profile_selector_unknown", f"Backup profile {selected} is not configured", "DATAOPS_BACKUP_PROFILE"))
        elif not profile_for_operation(profiles, operation, selected).can_backup:
            issues.append(ConfigurationIssue("profile_role_disallows_backup", f"Profile {selected} cannot publish backups", "role"))
        source_selected = selectors.get("backup_source")
        if source_selected:
            source_profile = profile_for_operation(profiles, operation, source_selected)
            if source_profile is None:
                issues.append(ConfigurationIssue("profile_selector_unknown", f"Backup source profile {source_selected} is not configured", "DATAOPS_BACKUP_SOURCE_PROFILE"))
            elif not source_profile.can_restore:
                issues.append(ConfigurationIssue("profile_role_disallows_source", f"Profile {source_selected} cannot be used as a backup source", "role"))
    if operation == "restore":
        selected = selectors.get("restore_source") or selectors.get("restore")
        if not selected:
            issues.append(ConfigurationIssue("profile_selector_missing", "Restore profile selector is missing", "DATAOPS_RESTORE_PROFILE"))
        elif not profile_for_operation(profiles, operation, selected):
            issues.append(ConfigurationIssue("profile_selector_unknown", f"Restore profile {selected} is not configured", "DATAOPS_RESTORE_PROFILE"))
        elif not profile_for_operation(profiles, operation, selected).can_restore:
            issues.append(ConfigurationIssue("profile_role_disallows_restore", f"Profile {selected} cannot provide restores", "role"))
        destination_selected = selectors.get("restore_destination")
        if destination_selected:
            destination_profile = profile_for_operation(profiles, operation, destination_selected)
            if destination_profile is None:
                issues.append(ConfigurationIssue("profile_selector_unknown", f"Restore destination profile {destination_selected} is not configured", "DATAOPS_RESTORE_DESTINATION_PROFILE"))
            elif not destination_profile.can_backup:
                issues.append(ConfigurationIssue("profile_role_disallows_backup", f"Profile {destination_selected} cannot receive restores", "role"))
    if source and destination:
        same_bucket = (source.endpoint.rstrip("/").lower(), source.bucket, source.dataset_id) == (destination.endpoint.rstrip("/").lower(), destination.bucket, destination.dataset_id)
        if same_bucket and source.namespace == destination.namespace:
            issues.append(ConfigurationIssue("same_bucket_namespace_collision", "Source and destination share an object-store namespace", "namespace"))
        if local_dataset_id and destination.dataset_id != local_dataset_id:
            issues.append(ConfigurationIssue("dataset_mismatch", "Destination dataset does not match the active dataset", "dataset_id"))
    return tuple(issues)


def validate_operation_route(source: ResolvedProfile | None, destination: ResolvedProfile | None, *, operation: str, local_dataset_id: str = "") -> tuple[ConfigurationIssue, ...]:
    issues: list[ConfigurationIssue] = []
    if operation in {"restore", "copy", "transfer"} and source is None:
        issues.append(ConfigurationIssue("source_profile_missing", "A source profile is required", "source_profile"))
    if operation in {"backup", "copy", "transfer"} and destination is None:
        issues.append(ConfigurationIssue("destination_profile_missing", "A destination profile is required", "destination_profile"))
    if source and operation in {"restore", "copy", "transfer"} and not source.can_restore:
        issues.append(ConfigurationIssue("profile_role_disallows_restore", f"Profile {source.key} cannot be used as a source", "role"))
    if source and operation == "backup" and not source.can_restore:
        issues.append(ConfigurationIssue("profile_role_disallows_source", f"Profile {source.key} cannot be used as a backup source", "role"))
    if destination and operation in {"backup", "copy", "transfer"} and not destination.can_backup:
        issues.append(ConfigurationIssue("profile_role_disallows_backup", f"Profile {destination.key} cannot be used as a destination", "role"))
    if destination and operation == "restore" and not destination.can_backup:
        issues.append(ConfigurationIssue("profile_role_disallows_backup", f"Profile {destination.key} cannot receive a restore", "role"))
    if source and destination:
        issues += list(validate_profiles((source, destination), source=source, destination=destination, local_dataset_id=local_dataset_id))
    return tuple(issues)


def resolve_setting(name: str, stored: object | None = None, default: object | None = None, environ: Mapping[str, str] | None = None) -> tuple[object, str]:
    """Return ``(value, source)`` with ENV > stored > default precedence."""
    env = os.environ if environ is None else environ
    if name in env and str(env[name]).strip() != "":
        return env[name], "environment"
    if stored is not None and str(stored).strip() != "":
        return stored, "stored"
    return default, "default"


def validate_legacy_environment(environ: Mapping[str, str] | None = None) -> None:
    """Reject retired generic aliases while keeping ARTIFACT_VAULT compatible.

    ``ARTIFACT_VAULT_*`` is intentionally accepted during the migration
    window and is projected into a named compatibility profile above.
    """
    env = os.environ if environ is None else environ
    legacy = sorted(key for key in env if key.startswith("VAULT_") and not key.startswith("VAULT_SYNC_"))
    if legacy:
        raise ImproperlyConfigured("Retired Data Operations environment keys present: " + ", ".join(legacy))
