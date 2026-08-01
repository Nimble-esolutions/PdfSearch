"""Constrained S3-compatible access for Data Operations observations."""

from __future__ import annotations

import json
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
    prefix = profile.credential_prefix
    access_key = environ.get(f"{prefix}_ACCESS_KEY", "") if prefix else ""
    secret_key = environ.get(f"{prefix}_SECRET_KEY", "") if prefix else ""
    if not access_key or not secret_key:
        raise StorageConfigurationError("profile credentials are not configured")
    try:
        import boto3
    except ImportError as exc:
        raise StorageConfigurationError("boto3 is not installed") from exc
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=profile.region or None,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )


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
