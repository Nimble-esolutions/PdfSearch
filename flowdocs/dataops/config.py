"""ENV-first configuration resolver for Data Operations.

Environment values are immutable at runtime and take precedence over stored
fallbacks.  ``resolve_profiles`` returns redacted dictionaries suitable for
the UI; credentials are never included.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from typing import Mapping

from django.core.exceptions import ImproperlyConfigured


_ID_RE = re.compile(r"[^A-Z0-9]+")
_BOOLS = {"1": True, "true": True, "yes": True, "on": True, "0": False, "false": False, "no": False, "off": False}


def _env_bool(name: str, default: bool = False, environ: Mapping[str, str] | None = None) -> bool:
    value = (environ or os.environ).get(name)
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
    environment_locked: bool = True

    @property
    def fingerprint(self) -> str:
        payload = "|".join((self.key, self.endpoint, self.bucket, self.region, self.dataset_id, self.source_id))
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
            "environment_locked": self.environment_locked,
            "fingerprint": self.fingerprint,
        }


def resolve_profiles(environ: Mapping[str, str] | None = None) -> tuple[ResolvedProfile, ...]:
    env = environ or os.environ
    raw = env.get("DATAOPS_ENV_PROFILES", "").strip()
    if not raw:
        return ()
    profiles: list[ResolvedProfile] = []
    for key in (item.strip().lower() for item in raw.split(",")):
        if not key:
            continue
        token = _profile_token(key)
        prefix = f"DATAOPS_PROFILE_{token}_"
        role = env.get(prefix + "ROLE", "both").strip().lower()
        if role not in {"backup", "restore", "both"}:
            raise ImproperlyConfigured(f"{prefix}ROLE must be backup, restore or both")
        required = {name: env.get(prefix + name, "").strip() for name in ("BUCKET", "DATASET_ID")}
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ImproperlyConfigured(f"{prefix}{missing[0]} is required")
        profiles.append(
            ResolvedProfile(
                key=key,
                display_name=env.get(prefix + "DISPLAY_NAME", key.replace("_", " ").title()),
                role=role,
                endpoint=env.get(prefix + "ENDPOINT", "").strip(),
                bucket=required["BUCKET"],
                region=env.get(prefix + "REGION", "").strip(),
                dataset_id=required["DATASET_ID"],
                source_id=env.get(prefix + "SOURCE_ID", "").strip(),
                credential_prefix=env.get(prefix + "CREDENTIAL_PREFIX", "").strip(),
            )
        )
    return tuple(profiles)


def resolve_setting(name: str, stored: object | None = None, default: object | None = None, environ: Mapping[str, str] | None = None) -> tuple[object, str]:
    """Return ``(value, source)`` with ENV > stored > default precedence."""
    env = environ or os.environ
    if name in env:
        return env[name], "environment"
    if stored is not None:
        return stored, "stored"
    return default, "default"


def validate_legacy_environment(environ: Mapping[str, str] | None = None) -> None:
    """Fail closed when removed vault aliases are still present."""
    env = environ or os.environ
    legacy = sorted(key for key in env if key.startswith(("ARTIFACT_VAULT_", "VAULT_")))
    if legacy:
        raise ImproperlyConfigured("Removed Data Operations environment keys present: " + ", ".join(legacy))
