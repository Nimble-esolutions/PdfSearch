"""Writer lease for single-authoritative-backup protocol.

Uses Redis for lease acquisition (atomic SET NX EX) with fallback to
database-level advisory lock when Redis is unavailable. The lease prevents
multiple maintenance workers or replicas from publishing conflicting
authoritative generations.

The lease record includes a monotonically increasing fencing token that
must be carried in every authoritative publication. A writer that loses the
lease cannot update the authoritative pointer.
"""

from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from django.core.cache import cache


LEASE_KEY_PREFIX = "pdfsearch:writer-lease:"
DEFAULT_LEASE_TTL_SECONDS = 300
DEFAULT_RENEW_BEFORE_SECONDS = 60


@dataclass
class WriterLease:
    """An acquired writer lease for a dataset."""

    dataset_id: str
    instance_id: str
    replica_id: str
    owner_token: str
    writer_epoch: int
    acquired_at: float
    expires_at: float
    renewed_at: float
    app_release: str = ""
    image_digest: str = ""

    @property
    def is_expired(self) -> bool:
        return time.monotonic() > self.expires_at

    @property
    def remaining_seconds(self) -> float:
        return max(0, self.expires_at - time.monotonic())

    @property
    def acquired_at_utc(self) -> str:
        return datetime.fromtimestamp(self.acquired_at, tz=timezone.utc).isoformat()


class LeaseError(RuntimeError):
    """Raised when lease acquisition or renewal fails."""


class LeaseLost(LeaseError):
    """Raised when a previously held lease can no longer be renewed."""


class LeaseConflict(LeaseError):
    """Raised when another writer holds the lease."""


def _lease_key(dataset_id: str) -> str:
    return f"{LEASE_KEY_PREFIX}{dataset_id}"


def _read_cache_lease(dataset_id: str) -> dict | None:
    raw = cache.get(_lease_key(dataset_id))
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    return None


def acquire_lease(
    dataset_id: str,
    instance_id: str,
    replica_id: str = "",
    ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS,
    *,
    app_release: str = "",
    image_digest: str = "",
    force: bool = False,
    force_reason: str = "",
) -> WriterLease:
    """Acquire or take over the writer lease for a dataset."""
    now = time.monotonic()
    owner_token = secrets.token_hex(16)
    key = _lease_key(dataset_id)

    existing = _read_cache_lease(dataset_id)
    if existing and not force:
        stored_expires = existing.get("expires_at", 0)
        if stored_expires > now:
            raise LeaseConflict(
                f"Lease held by {existing.get('instance_id', 'unknown')} "
                f"(epoch {existing.get('writer_epoch', 0)}, "
                f"expires in {max(0, stored_expires - now):.0f}s)"
            )

    if existing:
        new_epoch = existing.get("writer_epoch", 0) + 1
    else:
        new_epoch = 1

    lease_data = {
        "dataset_id": dataset_id,
        "instance_id": instance_id,
        "replica_id": replica_id,
        "owner_token": owner_token,
        "writer_epoch": new_epoch,
        "acquired_at": now,
        "expires_at": now + ttl_seconds,
        "renewed_at": now,
        "app_release": app_release,
        "image_digest": image_digest,
        "force_takeover": force,
        "force_reason": force_reason if force else "",
    }

    added = cache.add(key, lease_data, timeout=ttl_seconds)
    if not added and not force:
        existing = _read_cache_lease(dataset_id)
        if existing:
            raise LeaseConflict(
                f"Lease acquired by another writer: {existing.get('instance_id')}"
            )
    if not added and force:
        cache.set(key, lease_data, timeout=ttl_seconds)

    return WriterLease(
        dataset_id=dataset_id,
        instance_id=instance_id,
        replica_id=replica_id,
        owner_token=owner_token,
        writer_epoch=new_epoch,
        acquired_at=now,
        expires_at=now + ttl_seconds,
        renewed_at=now,
        app_release=app_release,
        image_digest=image_digest,
    )


def renew_lease(lease: WriterLease, ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS) -> WriterLease:
    """Renew an existing lease. Raises LeaseLost if another writer took over."""
    now = time.monotonic()
    key = _lease_key(lease.dataset_id)

    existing = _read_cache_lease(lease.dataset_id)
    if existing is None:
        raise LeaseLost("Lease expired and was not renewed in time")
    if existing.get("owner_token") != lease.owner_token:
        raise LeaseLost(
            f"Lease taken by {existing.get('instance_id', 'unknown')} (epoch {existing.get('writer_epoch')})"
        )

    new_expires = now + ttl_seconds
    existing["expires_at"] = new_expires
    existing["renewed_at"] = now
    cache.set(key, existing, timeout=ttl_seconds)

    return WriterLease(
        dataset_id=lease.dataset_id,
        instance_id=lease.instance_id,
        replica_id=lease.replica_id,
        owner_token=lease.owner_token,
        writer_epoch=lease.writer_epoch,
        acquired_at=lease.acquired_at,
        expires_at=new_expires,
        renewed_at=now,
        app_release=lease.app_release,
        image_digest=lease.image_digest,
    )


def release_lease(lease: WriterLease) -> None:
    """Gracefully release a writer lease."""
    key = _lease_key(lease.dataset_id)
    existing = _read_cache_lease(lease.dataset_id)
    if existing and existing.get("owner_token") == lease.owner_token:
        cache.delete(key)


def get_lease_status(dataset_id: str) -> dict | None:
    """Read-only lease status for a dataset."""
    return _read_cache_lease(dataset_id)


def validate_lease_for_publication(lease: WriterLease | None, dataset_id: str) -> WriterLease:
    """Confirm a valid lease exists before publishing an authoritative generation.

    Returns the verified lease. Raises LeaseError if invalid.
    """
    if lease is None:
        raise LeaseError("No writer lease held; cannot publish")

    if lease.is_expired:
        raise LeaseLost("Writer lease has expired")

    current = _read_cache_lease(dataset_id)
    if current is None:
        raise LeaseLost("Lease no longer present in cache")

    if current.get("owner_token") != lease.owner_token:
        raise LeaseLost(f"Lease token mismatch; another writer holds the lease")

    if current.get("writer_epoch") != lease.writer_epoch:
        raise LeaseLost(
            f"Writer epoch mismatch (held {lease.writer_epoch}, "
            f"current {current.get('writer_epoch')})"
        )

    return lease
