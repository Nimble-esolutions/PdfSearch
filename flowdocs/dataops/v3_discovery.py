"""Signed remote recovery-point discovery for a fresh control database."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping

from django.db import transaction
from django.utils import timezone

from .models import DataConnection, RecoveryPoint
from .v3_backup import dataset_root
from .v3_config import connection_from_model
from .v3_restore import V3RestoreError, load_verified_recovery_point
from .v3_storage import client_for_connection, read_object


_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class V3DiscoveryError(RuntimeError):
    """Typed, secret-free failure raised while projecting remote authority."""

    def __init__(self, code: str, *, retryable: bool = False):
        self.code = code
        self.retryable = retryable
        super().__init__(code)


def _json_object(body: bytes, code: str) -> dict:
    try:
        value = json.loads(body)
    except (TypeError, ValueError) as exc:
        raise V3DiscoveryError(code) from exc
    if not isinstance(value, Mapping):
        raise V3DiscoveryError(code)
    return dict(value)


def _safe_segment(value, code: str) -> str:
    segment = str(value or "").strip()
    if not _SAFE_SEGMENT.fullmatch(segment):
        raise V3DiscoveryError(code)
    return segment


def _sha256(value, code: str) -> str:
    digest = str(value or "").strip().lower()
    if not _SHA256.fullmatch(digest):
        raise V3DiscoveryError(code)
    return digest


def discover_latest_recovery_point(
    connection: DataConnection,
    *,
    signing_key: bytes,
    client_factory=client_for_connection,
) -> RecoveryPoint:
    """Verify the owned remote latest pointer before creating a local projection.

    This is the bootstrap path used when the data and control volumes are both
    fresh.  Remote storage is read-only throughout discovery; the local row is
    written only after the descriptor, manifest contract, and signature agree.
    """

    if not connection.pk or not connection.enabled:
        raise V3DiscoveryError("owned_connection_unavailable")
    if not connection.capabilities.get("read"):
        raise V3DiscoveryError("owned_connection_not_readable")
    if not signing_key:
        raise V3DiscoveryError("manifest_signing_key_missing")

    view = connection_from_model(connection)
    root = dataset_root(view)
    pointer_key = f"{root}/refs/latest.json"
    try:
        client = client_factory(view)
        pointer = _json_object(
            read_object(client, bucket=view.bucket, key=pointer_key),
            "latest_pointer_invalid",
        )
    except V3DiscoveryError:
        raise
    except Exception as exc:
        raise V3DiscoveryError("latest_pointer_read_failed", retryable=True) from exc

    recovery_point_id = _safe_segment(
        pointer.get("recovery_point_id"), "latest_pointer_invalid"
    )
    manifest_digest = _sha256(
        pointer.get("manifest_sha256"), "latest_pointer_invalid"
    )
    recovery_point_key = (
        f"{root}/recovery-points/{recovery_point_id}.json"
    )
    if (
        pointer.get("schema_version") != 3
        or pointer.get("dataset_id") != connection.dataset_id
        or pointer.get("recovery_point_key") != recovery_point_key
    ):
        raise V3DiscoveryError("latest_pointer_mismatch")

    try:
        descriptor = _json_object(
            read_object(
                client,
                bucket=view.bucket,
                key=recovery_point_key,
            ),
            "recovery_point_descriptor_invalid",
        )
    except V3DiscoveryError:
        raise
    except Exception as exc:
        raise V3DiscoveryError(
            "recovery_point_descriptor_read_failed", retryable=True
        ) from exc

    signature_key_id = str(descriptor.get("signature_key_id") or "").strip()
    if not signature_key_id:
        raise V3DiscoveryError("manifest_signature_key_missing")
    candidate = RecoveryPoint(
        connection=connection,
        profile_key="",
        dataset_id=connection.dataset_id,
        release_id=recovery_point_id,
        format_version=3,
        prefix=recovery_point_key,
        manifest_digest=manifest_digest,
        signature_key_id=signature_key_id,
        data_complete=True,
        activation_ready=False,
        state=RecoveryPoint.State.VERIFIED,
    )
    try:
        verified = load_verified_recovery_point(
            candidate,
            signing_key=signing_key,
            client_factory=lambda _connection: client,
        )
    except V3RestoreError as exc:
        raise V3DiscoveryError(
            exc.code,
            retryable=bool(getattr(exc, "retryable", False)),
        ) from exc

    manifest = verified.manifest
    activation_ready = all(
        not component.get("rebuild_required", False)
        for component in manifest["components"].values()
    )
    projection = {
        "prefix": recovery_point_key,
        "manifest_digest": manifest_digest,
        "signature_key_id": signature_key_id,
        "data_complete": True,
        "activation_ready": activation_ready,
        "state": RecoveryPoint.State.VERIFIED,
        "counts": dict(manifest["counts"]),
        "identity": {
            "release": manifest["application"]["release_version"],
            "image_digest": manifest["application"]["image_digest"],
            "database_schema": manifest["application"]["database_schema"],
        },
        "evidence": {
            "signature_valid": True,
            "discovered_from": "owned_latest_pointer",
            "pointer_key": pointer_key,
            "lineage": dict(manifest.get("lineage") or {}),
        },
        "observed_at": timezone.now(),
    }
    with transaction.atomic(using="control"):
        point, created = RecoveryPoint.objects.using("control").get_or_create(
            connection=connection,
            profile_key="",
            dataset_id=connection.dataset_id,
            release_id=recovery_point_id,
            defaults={"format_version": 3, **projection},
        )
        if not created:
            immutable_projection = {
                "format_version": point.format_version,
                "prefix": point.prefix,
                "manifest_digest": point.manifest_digest,
                "signature_key_id": point.signature_key_id,
            }
            expected_projection = {
                "format_version": 3,
                "prefix": recovery_point_key,
                "manifest_digest": manifest_digest,
                "signature_key_id": signature_key_id,
            }
            if immutable_projection != expected_projection:
                raise V3DiscoveryError("recovery_point_projection_conflict")
            for field, value in projection.items():
                setattr(point, field, value)
            point.save(
                using="control",
                update_fields=[*projection, "updated_at"],
            )
    return point
