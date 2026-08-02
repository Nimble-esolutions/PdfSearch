"""Minimal, secret-free configuration compiler for Data Operations v3."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from typing import Any, Mapping


CONFIG_VERSION = 3


class V3ConfigurationError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ConnectionView:
    public_id: str
    name: str
    provider: str
    endpoint: str
    bucket: str
    region: str
    prefix: str
    dataset_id: str
    credential_ref: str
    capabilities: Mapping[str, bool]
    source: str

    def redacted(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["credential_ref"] = self.credential_ref
        return payload


@dataclass(frozen=True)
class RuntimeConfig:
    version: int
    environment: str
    deployment_id: str
    dataset_id: str
    enabled: bool
    connection: ConnectionView | None
    policy: Mapping[str, Any]
    digest: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalized_environment(value: str) -> str:
    environment = str(value or "").strip().lower()
    return {"dev": "development", "stage": "staging", "prod": "production"}.get(
        environment,
        environment,
    )


def default_policy(environment: str) -> dict[str, Any]:
    """Return tested policy defaults; tuning is not routine ENV wiring."""

    environment = _normalized_environment(environment)
    common = {
        "backup": {
            "mode": "manual",
            "quiet_period_seconds": 120,
            "maximum_delay_seconds": 900,
        },
        "resources": {
            "parallel_hashers": 2,
            "parallel_transfers": 4,
            "retry_limit": 5,
        },
        "restore": {
            "retain_failed_candidates_days": 30,
            "require_indexing_ratio": 1.0,
        },
    }
    if environment == "production":
        common["backup"].update({"mode": "scheduled", "interval_seconds": 900})
        common["retention"] = {"daily": 14, "weekly": 8, "monthly": 12}
        common["restore"].update(
            {
                "pre_restore_backup": True,
                "mutation_barrier": True,
                "signed_activation": True,
            }
        )
    elif environment == "staging":
        common["retention"] = {"latest": 7, "weekly": 4}
        common["restore"].update(
            {"pre_restore_backup": False, "signed_activation": True}
        )
    else:
        common["retention"] = {"latest": 3, "days": 7}
        common["restore"].update(
            {"pre_restore_backup": False, "signed_activation": False}
        )
    return common


def connection_from_model(connection) -> ConnectionView:
    return ConnectionView(
        public_id=str(connection.public_id),
        name=connection.name,
        provider=connection.provider,
        endpoint=connection.endpoint,
        bucket=connection.bucket,
        region=connection.region,
        prefix=connection.prefix,
        dataset_id=connection.dataset_id,
        credential_ref=connection.credential_ref,
        capabilities=dict(connection.capabilities or {}),
        source="control_database",
    )


def bootstrap_connection_from_environment(
    environment: Mapping[str, str] | None = None,
    *,
    dataset_id: str,
) -> ConnectionView | None:
    """Compile the temporary ARTIFACT_VAULT bootstrap without reading secrets.

    The credential alias tells the worker which existing secret pair to
    resolve.  Values are deliberately never copied into this object.
    """

    values = os.environ if environment is None else environment
    endpoint = str(values.get("ARTIFACT_VAULT_ENDPOINT", "") or "").strip()
    bucket = str(values.get("ARTIFACT_VAULT_BUCKET", "") or "").strip()
    if not endpoint and not bucket:
        return None
    if not endpoint:
        raise V3ConfigurationError("owned_connection_endpoint_missing")
    if not bucket:
        raise V3ConfigurationError("owned_connection_bucket_missing")
    credential_present = bool(
        str(values.get("ARTIFACT_VAULT_ACCESS_KEY", "") or "").strip()
        and str(values.get("ARTIFACT_VAULT_SECRET_KEY", "") or "").strip()
    )
    credential_ref = str(
        values.get("ARTIFACT_VAULT_CREDENTIAL_REF", "") or ""
    ).strip()
    if not credential_ref and credential_present:
        credential_ref = "env://ARTIFACT_VAULT"
    return ConnectionView(
        public_id="environment-bootstrap",
        name="Owned recovery storage",
        provider="rustfs",
        endpoint=endpoint,
        bucket=bucket,
        region=str(values.get("ARTIFACT_VAULT_REGION", "") or "").strip(),
        prefix="v3",
        dataset_id=str(dataset_id or "").strip(),
        credential_ref=credential_ref,
        capabilities={
            "probed": False,
            "credential_available": credential_present or bool(credential_ref),
            "read": False,
            "write": False,
            "conditional_write": False,
        },
        source="environment_bootstrap",
    )


def compile_runtime_config(
    *,
    environment: str,
    deployment_id: str,
    dataset_id: str,
    enabled: bool,
    connection: ConnectionView | None,
    policy: Mapping[str, Any] | None = None,
) -> RuntimeConfig:
    normalized_environment = _normalized_environment(environment)
    if normalized_environment not in {
        "development",
        "staging",
        "production",
        "review",
        "test",
    }:
        raise V3ConfigurationError("environment_unknown")
    if not str(deployment_id or "").strip():
        raise V3ConfigurationError("deployment_identity_missing")
    if not str(dataset_id or "").strip():
        raise V3ConfigurationError("dataset_identity_missing")
    if connection and connection.source == "control_database" and connection.dataset_id != dataset_id:
        raise V3ConfigurationError("primary_connection_dataset_mismatch")
    payload = {
        "version": CONFIG_VERSION,
        "environment": normalized_environment,
        "deployment_id": str(deployment_id).strip(),
        "dataset_id": str(dataset_id).strip(),
        "enabled": bool(enabled),
        "connection": connection,
        "policy": dict(policy or default_policy(normalized_environment)),
    }
    digest_payload = {
        **payload,
        "connection": connection.redacted() if connection else None,
    }
    digest = hashlib.sha256(
        json.dumps(
            digest_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    return RuntimeConfig(**payload, digest=digest)
