"""Server-authoritative maintenance worker availability evidence."""

from __future__ import annotations

import os
import time
from pathlib import Path

from django.conf import settings


def heartbeat_path() -> Path:
    return Path(settings.MAINTENANCE_WORKER_HEARTBEAT_PATH)


def write_worker_heartbeat(*, observed_at: float | None = None) -> None:
    """Atomically publish a secret-free heartbeat on the shared control volume."""
    target = heartbeat_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    timestamp = time.time() if observed_at is None else observed_at
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(f"{timestamp:.6f}\n", encoding="ascii")
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def maintenance_worker_capability(*, observed_at: float | None = None) -> dict:
    """Return bounded worker readiness evidence suitable for UI and gates."""
    now = time.time() if observed_at is None else observed_at
    path = heartbeat_path()
    try:
        modified_at = path.stat().st_mtime
        age_seconds = max(0.0, now - modified_at)
    except (OSError, ValueError):
        return {
            "state": "worker_offline",
            "available": False,
            "reason_code": "maintenance_worker_unavailable",
            "observed_at": None,
            "age_seconds": None,
        }

    available = (
        age_seconds
        <= settings.MAINTENANCE_WORKER_HEARTBEAT_MAX_AGE_SECONDS
    )
    return {
        "state": "available" if available else "worker_offline",
        "available": available,
        "reason_code": "" if available else "maintenance_worker_unavailable",
        "observed_at": modified_at,
        "age_seconds": round(age_seconds, 3),
    }
