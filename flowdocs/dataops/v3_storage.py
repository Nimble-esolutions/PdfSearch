"""S3-compatible client and immutable object helpers for DataOps v3."""

from __future__ import annotations

import json
import os
import hashlib
from pathlib import Path
from typing import Any, Mapping

from .v3_config import ConnectionView


class V3StorageError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _credential_file(reference: str) -> Path:
    if reference.startswith("secret://dataops/"):
        name = reference.removeprefix("secret://dataops/")
        if not name or "/" in name or name in {".", ".."}:
            raise V3StorageError("credential_reference_invalid")
        return Path("/run/secrets/dataops") / name
    if reference.startswith("file://"):
        path = Path(reference.removeprefix("file://"))
        try:
            path.resolve().relative_to(Path("/run/secrets").resolve())
        except ValueError as exc:
            raise V3StorageError("credential_reference_outside_secret_root") from exc
        return path
    raise V3StorageError("credential_reference_invalid")


def resolve_s3_credentials(
    reference: str,
    *,
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Resolve credentials at execution time without returning them to UI code."""

    values = os.environ if environment is None else environment
    reference = str(reference or "").strip()
    if reference == "aws-sdk://default":
        return {}
    if reference == "env://ARTIFACT_VAULT":
        access = str(values.get("ARTIFACT_VAULT_ACCESS_KEY", "") or "").strip()
        secret = str(values.get("ARTIFACT_VAULT_SECRET_KEY", "") or "").strip()
        if not access or not secret:
            raise V3StorageError("credential_unavailable")
        result = {"aws_access_key_id": access, "aws_secret_access_key": secret}
        session = str(values.get("ARTIFACT_VAULT_SESSION_TOKEN", "") or "").strip()
        if session:
            result["aws_session_token"] = session
        return result
    path = _credential_file(reference)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise V3StorageError("credential_unavailable") from exc
    except (OSError, ValueError) as exc:
        raise V3StorageError("credential_file_invalid") from exc
    if not isinstance(payload, Mapping):
        raise V3StorageError("credential_file_invalid")
    access = str(payload.get("access_key") or "").strip()
    secret = str(payload.get("secret_key") or "").strip()
    if not access or not secret:
        raise V3StorageError("credential_file_invalid")
    result = {"aws_access_key_id": access, "aws_secret_access_key": secret}
    session = str(payload.get("session_token") or "").strip()
    if session:
        result["aws_session_token"] = session
    return result


def client_for_connection(connection: ConnectionView):
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:
        raise V3StorageError("s3_runtime_unavailable") from exc
    credentials = resolve_s3_credentials(connection.credential_ref)
    return boto3.client(
        "s3",
        endpoint_url=connection.endpoint,
        region_name=connection.region or "us-east-1",
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            retries={"max_attempts": 5, "mode": "standard"},
        ),
        **credentials,
    )


def error_code(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, Mapping):
        error = response.get("Error")
        if isinstance(error, Mapping):
            return str(error.get("Code") or "")
    return ""


def head_or_none(client, *, bucket: str, key: str):
    try:
        return client.head_object(Bucket=bucket, Key=key)
    except Exception as exc:
        if error_code(exc) in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise V3StorageError("storage_head_failed") from exc


def read_object(client, *, bucket: str, key: str) -> bytes:
    try:
        response = client.get_object(Bucket=bucket, Key=key)
        body = response["Body"]
        return body.read() if hasattr(body, "read") else bytes(body)
    except Exception as exc:
        raise V3StorageError("storage_read_failed") from exc


def verify_remote_object(
    client,
    *,
    bucket: str,
    key: str,
    sha256: str,
    size: int,
) -> None:
    body = None
    try:
        response = client.get_object(Bucket=bucket, Key=key)
        body = response["Body"]
        hasher = hashlib.sha256()
        observed_size = 0
        while True:
            chunk = body.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
            observed_size += len(chunk)
            if observed_size > size:
                raise V3StorageError("remote_object_digest_mismatch")
    except V3StorageError:
        raise
    except Exception as exc:
        raise V3StorageError("storage_read_failed") from exc
    finally:
        close = getattr(body, "close", None)
        if callable(close):
            close()
    if observed_size != size or hasher.hexdigest() != sha256:
        raise V3StorageError("remote_object_digest_mismatch")


def verify_head(
    head: Mapping[str, Any] | None,
    *,
    sha256: str,
    size: int,
) -> bool:
    """Verify size and, when available, the provider's digest metadata.

    Some S3-compatible providers accept user metadata but omit it from HEAD
    responses.  Returning ``False`` lets callers perform a full content hash
    readback without weakening immutable-object verification.
    """
    if head is None:
        raise V3StorageError("immutable_object_missing")
    metadata = {
        str(name).lower(): value
        for name, value in (head.get("Metadata") or {}).items()
    }
    observed_digest = str(metadata.get("sha256") or "").lower()
    observed_size = head.get("ContentLength")
    if observed_size != size or (observed_digest and observed_digest != sha256):
        raise V3StorageError("immutable_object_conflict")
    return bool(observed_digest)


def verify_immutable_object(
    client,
    *,
    bucket: str,
    key: str,
    sha256: str,
    size: int,
    head: Mapping[str, Any] | None = None,
) -> None:
    """Verify an immutable object, hashing content when metadata is absent."""

    current = head if head is not None else head_or_none(
        client,
        bucket=bucket,
        key=key,
    )
    if not verify_head(current, sha256=sha256, size=size):
        verify_remote_object(
            client,
            bucket=bucket,
            key=key,
            sha256=sha256,
            size=size,
        )


def put_bytes_immutable(
    client,
    *,
    bucket: str,
    key: str,
    body: bytes,
    sha256: str,
    content_type: str,
) -> str:
    if hashlib.sha256(body).hexdigest() != sha256:
        raise V3StorageError("immutable_object_digest_mismatch")
    existing = head_or_none(client, bucket=bucket, key=key)
    if existing is not None:
        verify_immutable_object(
            client,
            bucket=bucket,
            key=key,
            sha256=sha256,
            size=len(body),
            head=existing,
        )
        return "reused"
    try:
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentLength=len(body),
            ContentType=content_type,
            Metadata={"sha256": sha256, "immutable": "true"},
            IfNoneMatch="*",
        )
    except Exception as exc:
        try:
            existing = head_or_none(client, bucket=bucket, key=key)
            verify_immutable_object(
                client,
                bucket=bucket,
                key=key,
                sha256=sha256,
                size=len(body),
                head=existing,
            )
        except V3StorageError:
            raise V3StorageError("immutable_object_write_failed") from exc
        return "reused_after_race"
    verify_immutable_object(
        client,
        bucket=bucket,
        key=key,
        sha256=sha256,
        size=len(body),
    )
    return "uploaded"


def put_file_immutable(
    client,
    *,
    bucket: str,
    key: str,
    path: Path,
    sha256: str,
    size: int,
    content_type: str = "application/octet-stream",
) -> str:
    hasher = hashlib.sha256()
    observed_size = 0
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                hasher.update(chunk)
                observed_size += len(chunk)
    except OSError as exc:
        raise V3StorageError("immutable_source_read_failed") from exc
    if observed_size != size or hasher.hexdigest() != sha256:
        raise V3StorageError("immutable_object_digest_mismatch")
    existing = head_or_none(client, bucket=bucket, key=key)
    if existing is not None:
        verify_immutable_object(
            client,
            bucket=bucket,
            key=key,
            sha256=sha256,
            size=size,
            head=existing,
        )
        return "reused"
    try:
        with path.open("rb") as stream:
            client.put_object(
                Bucket=bucket,
                Key=key,
                Body=stream,
                ContentLength=size,
                ContentType=content_type,
                Metadata={"sha256": sha256, "immutable": "true"},
                IfNoneMatch="*",
            )
    except Exception as exc:
        try:
            existing = head_or_none(client, bucket=bucket, key=key)
            verify_immutable_object(
                client,
                bucket=bucket,
                key=key,
                sha256=sha256,
                size=size,
                head=existing,
            )
        except V3StorageError:
            raise V3StorageError("immutable_object_write_failed") from exc
        return "reused_after_race"
    verify_immutable_object(
        client,
        bucket=bucket,
        key=key,
        sha256=sha256,
        size=size,
    )
    return "uploaded"
