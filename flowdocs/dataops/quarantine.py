"""Bounded retention cleanup for verified mirror-deletion quarantines."""

from __future__ import annotations

import os
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .config import profile_map, resolve_profiles
from .job_executor import _objects, _prefix
from .models import DataOperation, DataOpsAuditEvent, MirrorDeletionPreview
from .storage import client_for_profile


def cleanup_mirror_quarantines(*, now=None, environ=None, clients=None) -> dict:
    now = now or timezone.now()
    retention_days = max(1, int(getattr(settings, "DATAOPS_MIRROR_QUARANTINE_RETENTION_DAYS", 30)))
    max_objects = max(1, min(10000, int(getattr(settings, "DATAOPS_MIRROR_QUARANTINE_CLEANUP_MAX_OBJECTS", 100))))
    expired_previews = MirrorDeletionPreview.objects.using("control").filter(
        state=MirrorDeletionPreview.State.READY, expires_at__lte=now
    ).update(state=MirrorDeletionPreview.State.EXPIRED, updated_at=now)
    cutoff = now - timedelta(days=retention_days)
    profiles = profile_map(resolve_profiles(dict(os.environ) if environ is None else environ))
    removed = scanned = 0
    for operation in DataOperation.objects.using("control").filter(
        kind=DataOperation.Kind.SYNC, state=DataOperation.State.SUCCEEDED, finished_at__lte=cutoff
    ).order_by("finished_at"):
        result = dict(operation.result or {})
        deletion = dict(result.get("deletion") or {})
        quarantine_prefix = str(deletion.get("quarantine_prefix", ""))
        if not quarantine_prefix or deletion.get("cleanup_completed_at"):
            continue
        profile = profiles.get(operation.destination_profile_key)
        expected_prefix = _prefix(profile.prefix if profile else "") + f".dataops-quarantine/{operation.public_id}/"
        if profile is None or quarantine_prefix != expected_prefix:
            DataOpsAuditEvent.objects.using("control").create(
                operation_id=operation.public_id, action="mirror_quarantine_cleanup_blocked", outcome="rejected",
                evidence={"reason": "quarantine_prefix_invalid"},
            )
            continue
        client = (clients or {}).get(profile.key) if clients else None
        client = client or client_for_profile(profile, dict(os.environ) if environ is None else environ, allow_http=profile.endpoint.startswith("http://"))
        batch = list(_objects(client, profile.bucket, quarantine_prefix))[:max_objects]
        scanned += len(batch)
        for item in batch:
            client.delete_object(Bucket=profile.bucket, Key=str(item["Key"]))
            removed += 1
        remaining = next(iter(_objects(client, profile.bucket, quarantine_prefix)), None)
        if remaining is None:
            deletion["cleanup_completed_at"] = now.isoformat()
            deletion["cleanup_removed_objects"] = int(deletion.get("cleanup_removed_objects", 0)) + len(batch)
            result["deletion"] = deletion
            operation.result = result
            operation.save(update_fields=["result", "updated_at"])
        DataOpsAuditEvent.objects.using("control").create(
            operation_id=operation.public_id, profile_key=profile.key,
            action="mirror_quarantine_cleaned", outcome="completed" if remaining is None else "partial",
            evidence={"removed_objects": len(batch), "retention_days": retention_days},
        )
        if removed >= max_objects:
            break
    return {"expired_previews": expired_previews, "scanned": scanned, "removed": removed}
