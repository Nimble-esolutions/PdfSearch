"""Constrained S3-compatible access for Data Operations observations."""

from __future__ import annotations

import json
import re
import socket
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlparse

from .config import ResolvedProfile
from .recovery import RecoveryInspection, inspect_manifest


class StorageConfigurationError(ValueError):
    pass


class StorageObservationError(RuntimeError):
    pass


class StoragePermissionError(StorageObservationError):
    pass


@dataclass(frozen=True)
class ManifestObservation:
    key: str
    inspection: RecoveryInspection
    etag: str


def validate_endpoint(endpoint: str, *, allow_http: bool = False, resolve_dns: bool = False) -> str:
    """Validate an endpoint before boto3 can make a network request."""
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise StorageConfigurationError("S3 endpoint must be an absolute HTTP(S) URL")
    if parsed.scheme == "http" and not allow_http:
        raise StorageConfigurationError("HTTP S3 endpoints are disabled")
    if resolve_dns:
        try:
            addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, parsed.port or 443)}
        except OSError as exc:
            raise StorageConfigurationError("S3 endpoint DNS resolution failed") from exc
        if any(address in {"127.0.0.1", "::1"} or address.startswith(("10.", "192.168.", "169.254.")) for address in addresses):
            raise StorageConfigurationError("private S3 endpoints are disabled")
    return endpoint.rstrip("/")


def client_for_profile(profile: ResolvedProfile, environ: Mapping[str, str], *, allow_http: bool = False):
    if not profile.endpoint:
        raise StorageConfigurationError("profile endpoint is required")
    endpoint = validate_endpoint(profile.endpoint, allow_http=allow_http)
    try:
        from .credentials import CredentialConfigurationError, resolve_profile_credentials
    except ModuleNotFoundError as exc:
        raise StorageConfigurationError("credential resolver is not installed") from exc
    try:
        credentials = resolve_profile_credentials(profile, environ=environ)
    except CredentialConfigurationError as exc:
        raise StorageConfigurationError(str(exc)) from exc
    try:
        import boto3
    except ImportError as exc:
        raise StorageConfigurationError("boto3 is not installed") from exc
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=profile.region or None,
        aws_access_key_id=credentials.access_key,
        aws_secret_access_key=credentials.secret_key,
    )


def profile_namespace(profile: ResolvedProfile, *, fallback_to_key: bool = True) -> str:
    """Return a safe object-store namespace for one profile."""
    value = str(getattr(profile, "namespace", "") or getattr(profile, "prefix", "") or "").strip().strip("/")
    if value:
        candidate = value
    else:
        candidate = profile.key if fallback_to_key else ""
    if candidate and (
        not re.fullmatch(r"[a-z0-9][a-z0-9._/-]{0,199}", candidate)
        or any(part in {"", ".", ".."} for part in candidate.split("/"))
    ):
        raise StorageConfigurationError("profile_namespace_invalid")
    return candidate


def generation_prefix(profile: ResolvedProfile, release_id: str, *, legacy: bool = False) -> str:
    release = str(release_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}", release) or ".." in release:
        raise StorageConfigurationError("release_id_invalid")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", str(profile.dataset_id or "")):
        raise StorageConfigurationError("dataset_id_invalid")
    namespace = "" if legacy else profile_namespace(profile)
    parts = ["datasets", profile.dataset_id]
    if namespace:
        parts.append(namespace)
    parts.extend(("generations", release))
    return "/".join(parts) + "/"


def generation_manifest_key(profile: ResolvedProfile, release_id: str, *, legacy: bool = False) -> str:
    return generation_prefix(profile, release_id, legacy=legacy) + "manifest.json"


def _provider_code(exc: Exception) -> str:
    response = getattr(exc, "response", {}) or {}
    error = response.get("Error", {}) or {}
    return str(error.get("Code", ""))


def probe_profile_access(client: Any, profile: ResolvedProfile, *, require_write: bool = False) -> dict[str, Any]:
    """Check the exact permissions needed by a preflight without exposing errors."""
    try:
        client.head_bucket(Bucket=profile.bucket)
    except Exception as exc:
        code = _provider_code(exc)
        raise StoragePermissionError("destination_permission_denied" if require_write else "source_permission_denied") from exc
    if require_write:
        key = generation_prefix(profile, "preflight", legacy=not bool(profile.namespace)) + ".dataops-permission-check"
        try:
            client.put_object(Bucket=profile.bucket, Key=key, Body=b"dataops-preflight")
            delete = getattr(client, "delete_object", None)
            if delete:
                delete(Bucket=profile.bucket, Key=key)
        except Exception as exc:
            raise StoragePermissionError("destination_permission_denied") from exc
    return {"bucket": profile.bucket, "endpoint": profile.endpoint, "write": require_write}


def observe_manifests(client: Any, profile: ResolvedProfile, *, max_objects: int = 100) -> tuple[ManifestObservation, ...]:
    if max_objects < 1 or max_objects > 1000:
        raise StorageConfigurationError("max_objects must be between 1 and 1000")
    prefix = f"datasets/{profile.dataset_id}/"
    try:
        response = client.list_objects_v2(Bucket=profile.bucket, Prefix=prefix, MaxKeys=max_objects)
    except Exception as exc:
        raise StorageObservationError("object-store listing failed") from exc
    observations: list[ManifestObservation] = []
    for item in response.get("Contents", [])[:max_objects]:
        key = str(item.get("Key", ""))
        if not key.endswith("manifest.json"):
            continue
        try:
            body = client.get_object(Bucket=profile.bucket, Key=key)["Body"].read(8 * 1024 * 1024 + 1)
            if len(body) > 8 * 1024 * 1024:
                raise StorageObservationError("manifest exceeds maximum size")
            inspection = inspect_manifest(json.loads(body.decode("utf-8")))
        except StorageObservationError:
            raise
        except Exception as exc:
            raise StorageObservationError("manifest inspection failed") from exc
        observations.append(ManifestObservation(key, inspection, str(item.get("ETag", "")).strip('"')))
    return tuple(observations)
