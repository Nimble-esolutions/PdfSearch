"""Automatic, secret-free health evidence for the DataOps owned connection."""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from core.object_store_capabilities import probe_s3_capabilities

from .models import DataConnection
from .v3_config import connection_from_model
from .v3_storage import V3StorageError, client_for_connection


PROBE_MAX_AGE = timedelta(hours=1)


class V3ConnectionError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def connection_is_ready(connection: DataConnection, *, now=None) -> bool:
    now = now or timezone.now()
    capabilities = dict(connection.capabilities or {})
    return bool(
        connection.enabled
        and connection.last_probed_at
        and now - connection.last_probed_at <= PROBE_MAX_AGE
        and capabilities.get("probed") is True
        and capabilities.get("read") is True
        and capabilities.get("write") is True
        and capabilities.get("conditional_write") is True
    )


def probe_owned_connection(
    connection: DataConnection,
    *,
    deployment_id: str = "",
    client_factory=client_for_connection,
) -> DataConnection:
    """Probe actual S3 semantics and persist only typed, non-secret evidence."""

    checked_at = timezone.now()
    failure_codes: list[str] = []
    try:
        view = connection_from_model(connection)
        client = client_factory(view)
        result = probe_s3_capabilities(
            client=client,
            endpoint=view.endpoint,
            bucket=view.bucket,
            deployment_id=deployment_id,
        )
        readable = bool(result.bucket_accessible and result.read_after_write_consistent)
        writable = bool(
            readable and result.etag_available and result.metadata_supported
        )
        conditional = bool(result.authoritative_publication_allowed)
        failure_codes.extend(result.errors)
        if not readable:
            failure_codes.append("owned_connection_not_readable")
        if not writable:
            failure_codes.append("owned_connection_not_writable")
        if not conditional:
            failure_codes.append("conditional_writes_unsupported")
        capabilities = {
            "probed": True,
            "read": readable,
            "write": writable,
            "conditional_write": conditional,
            "etag": bool(result.etag_available),
            "metadata": bool(result.metadata_supported),
            "versioning": bool(result.bucket_versioning_enabled),
        }
    except V3StorageError as exc:
        failure_codes.append(exc.code)
        capabilities = {
            "probed": True,
            "read": False,
            "write": False,
            "conditional_write": False,
        }
    except Exception:
        failure_codes.append("connection_probe_failed")
        capabilities = {
            "probed": True,
            "read": False,
            "write": False,
            "conditional_write": False,
        }

    failure_codes = list(dict.fromkeys(failure_codes))
    healthy = bool(
        capabilities["read"]
        and capabilities["write"]
        and capabilities["conditional_write"]
    )
    connection.capabilities = capabilities
    connection.last_probed_at = checked_at
    connection.observation = {
        "status": "ready" if healthy else "blocked",
        "failure_codes": failure_codes,
        "checked_at": checked_at.isoformat(),
    }
    connection.save(
        using="control",
        update_fields=[
            "capabilities",
            "last_probed_at",
            "observation",
            "updated_at",
        ],
    )
    if not healthy:
        raise V3ConnectionError(
            failure_codes[0] if failure_codes else "owned_connection_not_ready"
        )
    return connection


def ensure_owned_connection_ready(
    connection: DataConnection,
    *,
    deployment_id: str = "",
    client_factory=client_for_connection,
) -> DataConnection:
    if connection_is_ready(connection):
        return connection
    return probe_owned_connection(
        connection,
        deployment_id=deployment_id,
        client_factory=client_factory,
    )
