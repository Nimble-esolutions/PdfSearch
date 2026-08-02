"""Preview and quarantine-first deletion controls for mirror jobs."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import timedelta

from django.utils import timezone

from .config import profile_map, resolve_profiles
from .job_executor import _objects, _prefix
from .models import BackupJob, MirrorDeletionPreview
from .pipeline import DataOpsPipelineError
from .storage import client_for_profile


def _context(job, environ=None, source_client=None, target_client=None):
    environment = dict(os.environ) if environ is None else dict(environ)
    profiles = profile_map(resolve_profiles(environment))
    source = profiles.get(job.source_profile_key)
    target = profiles.get(job.target_profile_key)
    if source is None or target is None:
        raise DataOpsPipelineError("job_profile_missing", stage="preflight", retryable=False)
    source_client = source_client or client_for_profile(source, environment, allow_http=source.endpoint.startswith("http://"))
    target_client = target_client or client_for_profile(target, environment, allow_http=target.endpoint.startswith("http://"))
    return source, target, source_client, target_client


def orphan_evidence(job, *, environ=None, source_client=None, target_client=None):
    source, target, source_client, target_client = _context(job, environ, source_client, target_client)
    source_prefix = _prefix(source.prefix) + _prefix(job.source_prefix)
    target_prefix = _prefix(target.prefix) + _prefix(job.target_prefix)
    expected = {target_prefix + str(item["Key"])[len(source_prefix):].lstrip("/") for item in _objects(source_client, source.bucket, source_prefix)}
    target_items = list(_objects(target_client, target.bucket, target_prefix))
    orphans = sorted(
        (str(item["Key"]), int(item.get("Size", 0)))
        for item in target_items
        if str(item["Key"]) not in expected and "/.dataops-quarantine/" not in str(item["Key"])
    )
    payload = json.dumps(orphans, separators=(",", ":"), ensure_ascii=True).encode()
    return {"keys": [key for key, _ in orphans], "digest": hashlib.sha256(payload).hexdigest(), "bytes": sum(size for _, size in orphans), "target_count": len(target_items)}


def create_deletion_preview(job, **kwargs):
    if job.mode != BackupJob.Mode.MIRROR or not job.delete_orphans:
        raise DataOpsPipelineError("mirror_deletion_not_configured", stage="preflight", retryable=False)
    evidence = orphan_evidence(job, **kwargs)
    count = len(evidence["keys"])
    percentage = (count * 100 / max(1, evidence["target_count"]))
    if count > job.mirror_delete_max_objects or percentage > job.mirror_delete_max_percent:
        raise DataOpsPipelineError("mirror_deletion_budget_exceeded", stage="preflight", retryable=False, details={"objects": count, "percent": round(percentage, 2)})
    return MirrorDeletionPreview.objects.using("control").create(
        job=job,
        digest=evidence["digest"],
        object_keys=evidence["keys"],
        target_object_count=evidence["target_count"],
        total_bytes=evidence["bytes"],
        expires_at=timezone.now() + timedelta(minutes=15),
    )


def apply_confirmed_deletions(operation, job, source_client, target_client, target, preview):
    if preview.state != MirrorDeletionPreview.State.READY or preview.expires_at <= timezone.now():
        raise DataOpsPipelineError("mirror_preview_expired", stage="verification", retryable=False)
    current = orphan_evidence(job, source_client=source_client, target_client=target_client)
    if current["digest"] != preview.digest or current["keys"] != preview.object_keys:
        raise DataOpsPipelineError("mirror_preview_stale", stage="verification", retryable=False)
    quarantine = _prefix(target.prefix) + f".dataops-quarantine/{operation.public_id}/"
    for key in preview.object_keys:
        quarantine_key = quarantine + key[len(_prefix(target.prefix)):].lstrip("/")
        original = target_client.head_object(Bucket=target.bucket, Key=key)
        target_client.copy_object(Bucket=target.bucket, Key=quarantine_key, CopySource={"Bucket": target.bucket, "Key": key})
        quarantined = target_client.head_object(Bucket=target.bucket, Key=quarantine_key)
        if int(original.get("ContentLength", -1)) != int(quarantined.get("ContentLength", -2)):
            raise DataOpsPipelineError("mirror_quarantine_verification_failed", stage="verification", retryable=False)
        target_client.delete_object(Bucket=target.bucket, Key=key)
    preview.state = MirrorDeletionPreview.State.APPLIED
    preview.save(update_fields=["state", "updated_at"])
    return {"deleted": len(preview.object_keys), "quarantine_prefix": quarantine, "preview_digest": preview.digest}
