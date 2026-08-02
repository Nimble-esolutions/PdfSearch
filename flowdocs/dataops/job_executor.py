"""Checkpointed, non-destructive S3-to-S3 job execution."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from .config import profile_map, resolve_profiles
from .models import BackupJob, MirrorDeletionPreview
from .pipeline import DataOpsPipelineError
from .storage import client_for_profile, probe_profile_access


def _prefix(value: str) -> str:
    value = str(value or "").strip().strip("/")
    return f"{value}/" if value else ""


def _objects(client: Any, bucket: str, prefix: str):
    token = None
    while True:
        request = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": 1000}
        if token:
            request["ContinuationToken"] = token
        response = client.list_objects_v2(**request)
        for item in response.get("Contents", []):
            key = str(item.get("Key", ""))
            if key and not key.endswith("/"):
                yield item
        if not response.get("IsTruncated"):
            return
        token = response.get("NextContinuationToken")
        if not token:
            raise DataOpsPipelineError("source_listing_invalid", stage="transfer", retryable=False)


def _target_key(source_key: str, source_prefix: str, target_prefix: str) -> str:
    relative = source_key[len(source_prefix):] if source_prefix and source_key.startswith(source_prefix) else source_key
    return target_prefix + relative.lstrip("/")


def execute_backup_job(
    operation,
    *,
    environ: Mapping[str, str] | None = None,
    source_client: Any = None,
    target_client: Any = None,
    sleeper: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    lease: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    checkpoint = dict(operation.checkpoint or {})
    slug = str(checkpoint.get("job_slug", ""))
    job = BackupJob.objects.using("control").filter(slug=slug, enabled=True).first()
    if job is None:
        raise DataOpsPipelineError("backup_job_missing_or_disabled", stage="preflight", retryable=False)
    preview = None
    if job.delete_orphans:
        preview_id = str(checkpoint.get("mirror_preview_id", ""))
        preview = MirrorDeletionPreview.objects.using("control").filter(public_id=preview_id, job=job).first()
        if preview is None:
            raise DataOpsPipelineError("mirror_preview_required", stage="preflight", retryable=False)
    if job.bandwidth_limit_bps:
        raise DataOpsPipelineError("bandwidth_limit_not_supported", stage="preflight", retryable=False)

    environment = dict(os.environ) if environ is None else dict(environ)
    profiles = profile_map(resolve_profiles(environment))
    source = profiles.get(job.source_profile_key)
    target = profiles.get(job.target_profile_key)
    if source is None or target is None:
        raise DataOpsPipelineError("job_profile_missing", stage="preflight", retryable=False)
    if source.role not in {"restore", "both"} or target.role not in {"backup", "both"}:
        raise DataOpsPipelineError("job_profile_role_invalid", stage="preflight", retryable=False)
    source_prefix = _prefix(source.prefix or source.namespace) + _prefix(job.source_prefix)
    target_prefix = _prefix(target.prefix or target.namespace) + _prefix(job.target_prefix)
    run_started = now()
    if job.mode == BackupJob.Mode.ARCHIVE:
        target_prefix = str(checkpoint.get("target_prefix") or (target_prefix + "snapshots/" + run_started.strftime("%Y%m%dT%H%M%SZ") + "/"))
    if source.endpoint == target.endpoint and source.bucket == target.bucket and (
        source_prefix.startswith(target_prefix) or target_prefix.startswith(source_prefix)
    ):
        raise DataOpsPipelineError("same_bucket_namespace_overlap", stage="preflight", retryable=False)

    source_client = source_client or client_for_profile(source, environment)
    target_client = target_client or client_for_profile(target, environment)
    probe_profile_access(source_client, source, require_write=False)
    probe_profile_access(target_client, target, require_write=True)

    try:
        from boto3.s3.transfer import TransferConfig
    except ImportError as exc:
        raise DataOpsPipelineError("multipart_support_missing", stage="preflight", retryable=False) from exc
    transfer_config = TransferConfig(
        multipart_threshold=job.multipart_threshold_bytes,
        multipart_chunksize=job.multipart_chunk_size_bytes,
        max_concurrency=job.max_parallel_transfers,
        use_threads=job.max_parallel_transfers > 1,
    )
    copied = skipped = bytes_copied = 0
    last_completed_key = str(checkpoint.get("last_completed_key", ""))
    for item in _objects(source_client, source.bucket, source_prefix):
        source_key = str(item["Key"])
        destination_key = _target_key(source_key, source_prefix, target_prefix)
        if last_completed_key and source_key <= last_completed_key:
            skipped += 1
            continue
        if lease:
            lease()
        source_size = int(item.get("Size", 0))
        source_etag = str(item.get("ETag", "")).strip('"')
        try:
            existing = target_client.head_object(Bucket=target.bucket, Key=destination_key)
        except Exception:
            existing = None
        if job.mode != BackupJob.Mode.ARCHIVE and existing and int(existing.get("ContentLength", -1)) == source_size and str((existing.get("Metadata") or {}).get("dataops-source-etag", "")) == source_etag:
            skipped += 1
            last_completed_key = source_key
            continue
        last_error = None
        for attempt in range(job.retry_limit + 1):
            try:
                body = source_client.get_object(Bucket=source.bucket, Key=source_key)["Body"]
                target_client.upload_fileobj(
                    body,
                    target.bucket,
                    destination_key,
                    ExtraArgs={"Metadata": {"dataops-source-etag": source_etag}},
                    Config=transfer_config,
                )
                observed = target_client.head_object(Bucket=target.bucket, Key=destination_key)
                if int(observed.get("ContentLength", -1)) != source_size:
                    raise ValueError("destination size mismatch")
                break
            except Exception as exc:
                last_error = exc
                if attempt >= job.retry_limit:
                    raise DataOpsPipelineError("object_transfer_failed", stage="transfer", retryable=False, details={"key": source_key, "attempts": attempt + 1}) from exc
                sleeper(min(30.0, 0.25 * (2**attempt)))
        if last_error is not None:
            last_error = None
        copied += 1
        bytes_copied += source_size
        last_completed_key = source_key
        operation.checkpoint = {**checkpoint, "job_slug": slug, "last_completed_key": last_completed_key, "copied": copied, "skipped": skipped, "bytes_copied": bytes_copied, "target_prefix": target_prefix}
        operation.pipeline_stage = "transfer"
        operation.save(update_fields=["checkpoint", "pipeline_stage", "updated_at"])

    receipt = {
        "job_slug": slug,
        "mode": job.mode,
        "source_profile": source.key,
        "target_profile": target.key,
        "source_prefix": source_prefix,
        "target_prefix": target_prefix,
        "copied": copied,
        "skipped": skipped,
        "bytes_copied": bytes_copied,
        "verified": copied + skipped,
        "delete_orphans": bool(preview),
        "published_at": now().isoformat(),
        "stages": [
            {"stage": "preflight", "status": "succeeded"},
            {"stage": "transfer", "status": "succeeded"},
            {"stage": "verification", "status": "succeeded"},
            {"stage": "publish_receipt", "status": "succeeded"},
        ],
    }
    if preview:
        from .mirror import apply_confirmed_deletions

        receipt["deletion"] = apply_confirmed_deletions(operation, job, source_client, target_client, target, preview)
    BackupJob.objects.using("control").filter(pk=job.pk).update(last_run_at=now(), last_run_status="succeeded")
    return receipt
