"""Backup policy engine and triggers.

Manages backup scheduling, dirty-state tracking, no-change detection,
minimum-interval enforcement, and debouncing. Integrates with the existing
MaintenanceJob queue for execution.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.utils import timezone as django_timezone

from .models import MaintenanceJob, MaintenanceAuditEvent


DIRTY_STATE_KEY = "pdfsearch:backup:dirty"
LAST_BACKUP_KEY = "pdfsearch:backup:last-attempt"
BACKUP_IDEMPOTENCY_PREFIX = "pdfsearch:backup:idem:"


def mark_data_dirty() -> None:
    """Record that data has changed since the last backup."""
    from django.core.cache import cache
    cache.set(DIRTY_STATE_KEY, int(datetime.now(timezone.utc).timestamp()), timeout=None)


def is_data_dirty() -> bool:
    """Check if data has changed since the last backup check."""
    from django.core.cache import cache
    return cache.get(DIRTY_STATE_KEY) is not None


def clear_dirty_flag() -> None:
    """Clear the dirty flag after a successful backup."""
    from django.core.cache import cache
    cache.delete(DIRTY_STATE_KEY)


def _backup_fingerprint() -> str:
    """Compute a lightweight fingerprint of the current data state."""
    from django.db import connection
    import sqlite3

    db_path = connection.settings_dict.get("NAME", "")
    if not db_path or not Path(db_path).is_file():
        return ""

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        page_count = conn.execute("PRAGMA page_count").fetchone()[0]
        schema_hash = conn.execute(
            "SELECT hex(sha256(group_concat(sql, ''))) FROM sqlite_master "
            "WHERE type IN ('table','index','trigger','view') ORDER BY name"
        ).fetchone()[0] or ""
        source = f"{page_count}:{schema_hash}"
        return hashlib.sha256(source.encode()).hexdigest()
    except Exception:
        return ""
    finally:
        conn.close()


def backup_needed(*, min_interval_seconds: int = 300) -> bool:
    """Determine if a backup is due.

    Returns False if:
    - A backup was attempted within min_interval_seconds
    - No data has changed since the last backup
    - A backup job is already queued/running
    """
    from django.core.cache import cache

    if not is_data_dirty():
        return False

    last_attempt_raw = cache.get(LAST_BACKUP_KEY)
    if last_attempt_raw:
        try:
            last_attempt = float(last_attempt_raw)
            elapsed = datetime.now(timezone.utc).timestamp() - last_attempt
            if elapsed < min_interval_seconds:
                return False
        except (TypeError, ValueError):
            pass

    existing = MaintenanceJob.objects.filter(
        kind__in=("sync_generation", "restore_generation"),
        status__in=("queued", "running"),
    ).exists()
    if existing:
        return False

    return True


def compute_fingerprint() -> str:
    """Compute current data fingerprint for no-change detection."""
    fp = _backup_fingerprint()
    from django.core.cache import cache
    previous = cache.get("pdfsearch:backup:last-fingerprint", "")
    cache.set("pdfsearch:backup:last-fingerprint", fp, timeout=None)
    return fp


def record_backup_attempt() -> None:
    """Record the timestamp of a backup attempt."""
    from django.core.cache import cache
    cache.set(LAST_BACKUP_KEY, str(datetime.now(timezone.utc).timestamp()), timeout=None)


def queue_backup_if_needed(*, requested_by=None, min_interval_seconds: int = 300) -> MaintenanceJob | None:
    """Queue a sync_generation job if a backup is needed.

    Returns the created job or None if no backup was needed.
    """
    if not backup_needed(min_interval_seconds=min_interval_seconds):
        record_backup_attempt()
        return None

    record_backup_attempt()

    fingerprint = compute_fingerprint()
    previous = _last_fingerprint()
    if fingerprint and previous and fingerprint == previous:
        clear_dirty_flag()
        return None

    job = MaintenanceJob.objects.create(
        kind="sync_generation",
        requested_by=requested_by,
        status="queued",
        total_items=1,
    )
    MaintenanceAuditEvent.objects.create(
        job=job,
        event_type="queued",
        actor=requested_by,
        payload={"trigger": "scheduled", "fingerprint": fingerprint[:16] if fingerprint else ""},
    )
    return job


def _last_fingerprint() -> str:
    from django.core.cache import cache
    return cache.get("pdfsearch:backup:last-fingerprint", "") or ""


def create_idempotency_key(prefix: str, generation_id: str) -> str:
    """Create an idempotency key for a backup operation."""
    return f"{BACKUP_IDEMPOTENCY_PREFIX}{prefix}:{generation_id}"


def is_duplicate_backup(idempotency_key: str) -> bool:
    """Check if a backup with this idempotency key already completed."""
    from django.core.cache import cache
    return cache.get(idempotency_key) is not None


def mark_backup_complete(idempotency_key: str, ttl: int = 86400) -> None:
    """Mark a backup as completed for idempotency tracking."""
    from django.core.cache import cache
    cache.set(idempotency_key, "1", timeout=ttl)
    clear_dirty_flag()
