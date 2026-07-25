import os
import uuid

from django.db import migrations


VAULT_JOB_KINDS = {
    "sync_generation",
    "restore_generation",
    "promote_generation",
    "rollback_generation",
    "purge_generation",
}

GENERATION_STATE_MAP = {
    "staged": ("candidate", "unknown"),
    "validated": ("candidate", "unknown"),
    "active": ("candidate", "unknown"),
    "superseded": ("candidate", "unknown"),
    "failed": ("invalid", "unknown"),
    "purged": ("retired", "unknown"),
}

JOB_STATE_MAP = {
    "queued": "queued",
    "running": "stale",
    "paused": "stale",
    "cancel_requested": "stale",
    "completed": "succeeded",
    "failed": "terminal_failed",
    "cancelled": "cancelled",
}


def backfill_legacy_lifecycle(apps, schema_editor):
    if schema_editor.connection.alias != "control":
        return

    LegacyGeneration = apps.get_model("core", "ArtifactGeneration")
    LegacyValidation = apps.get_model("core", "ArtifactValidation")
    LegacyJob = apps.get_model("core", "MaintenanceJob")
    LegacyAudit = apps.get_model("core", "MaintenanceAuditEvent")
    LegacyUser = apps.get_model("core", "CustomUser")

    Profile = apps.get_model("vaultops", "VaultConnectionProfile")
    Generation = apps.get_model("vaultops", "ArtifactGeneration")
    Validation = apps.get_model("vaultops", "ArtifactValidation")
    Job = apps.get_model("vaultops", "VaultJob")
    Audit = apps.get_model("vaultops", "VaultAuditEvent")

    control = "control"
    application = "default"
    dataset_id = os.getenv("DATASET_ID", "legacy-default")
    profile, _ = Profile.objects.using(control).get_or_create(
        key="legacy-default",
        defaults={
            "display_name": "Legacy application database",
            "source": "legacy",
            "enabled": True,
            "read_only": True,
            "environment_locked": True,
            "dataset_id": dataset_id,
        },
    )

    user_names = {
        user_id: username
        for user_id, username in LegacyUser.objects.using(application).values_list(
            "id", "username"
        )
    }
    generation_ids = {}
    unverified_active = []
    for legacy in LegacyGeneration.objects.using(application).all().iterator():
        manifest = legacy.manifest if isinstance(legacy.manifest, dict) else {}
        projected_dataset = manifest.get("dataset_id") or dataset_id
        vault_state, runtime_state = GENERATION_STATE_MAP.get(
            legacy.status, ("unknown", "unknown")
        )
        generation, _ = Generation.objects.using(control).update_or_create(
            profile=profile,
            dataset_id=projected_dataset,
            generation_id=legacy.generation_id,
            defaults={
                "manifest": manifest,
                "manifest_digest": manifest.get("manifest_digest", ""),
                "vault_state": vault_state,
                "runtime_state": runtime_state,
                "local_presence": "unknown",
                "source": legacy.source,
                "legacy_database_id": legacy.pk,
                "legacy_status": legacy.status,
                "observed_at": legacy.created_at,
            },
        )
        generation_ids[legacy.pk] = generation.pk
        if legacy.status == "active":
            unverified_active.append(legacy.generation_id)

    for legacy in LegacyValidation.objects.using(application).all().iterator():
        generation_pk = generation_ids.get(legacy.generation_id)
        if generation_pk is None:
            continue
        details = legacy.details if isinstance(legacy.details, dict) else {}
        Validation.objects.using(control).update_or_create(
            legacy_database_id=legacy.pk,
            defaults={
                "generation_id": generation_pk,
                "validation_type": legacy.validation_type,
                "status": legacy.status,
                "manifest_digest": details.get("manifest_digest", ""),
                "validator_version": "legacy",
                "reason_codes": ["legacy_validation_unverified"],
                "evidence": details,
                "validated_by_id": legacy.validated_by_id,
                "validated_by_name": user_names.get(legacy.validated_by_id, ""),
            },
        )

    job_ids = {}
    legacy_jobs = LegacyJob.objects.using(application).filter(
        kind__in=VAULT_JOB_KINDS
    )
    for legacy in legacy_jobs.iterator():
        scope = legacy.scope if isinstance(legacy.scope, dict) else {}
        options = legacy.options if isinstance(legacy.options, dict) else {}
        generation_id = (
            scope.get("generation_id") or options.get("generation_id") or ""
        )
        status = JOB_STATE_MAP.get(legacy.status, "terminal_failed")
        safe_error_code = ""
        if legacy.status == "failed":
            safe_error_code = "legacy_job_failed"
        elif status == "stale":
            safe_error_code = "legacy_job_owner_unverified"
        job, _ = Job.objects.using(control).update_or_create(
            operation=legacy.kind,
            idempotency_key=f"legacy:{legacy.pk}",
            defaults={
                "status": status,
                "profile_id": profile.pk,
                "dataset_id": scope.get("dataset_id") or dataset_id,
                "generation_id": generation_id,
                "progress": {
                    "total_items": legacy.total_items,
                    "completed_items": legacy.completed_items,
                    "failed_items": legacy.failed_items,
                },
                "requested_by_id": legacy.requested_by_id,
                "requested_by_name": user_names.get(legacy.requested_by_id, ""),
                "legacy_database_id": legacy.pk,
                "safe_error_code": safe_error_code,
                "started_at": legacy.started_at,
                "finished_at": legacy.finished_at,
            },
        )
        job_ids[legacy.pk] = job.public_id

    for legacy in LegacyAudit.objects.using(application).filter(
        job_id__in=job_ids
    ).iterator():
        payload = legacy.payload if isinstance(legacy.payload, dict) else {}
        Audit.objects.using(control).get_or_create(
            legacy_database_id=legacy.pk,
            defaults={
                "public_id": uuid.uuid4(),
                "actor_id": legacy.actor_id,
                "actor_name": user_names.get(legacy.actor_id, ""),
                "correlation_id": uuid.uuid4(),
                "job_public_id": job_ids.get(legacy.job_id),
                "action": f"legacy_{legacy.event_type}",
                "reason_code": "legacy_projection",
                "before_state": {},
                "after_state": {},
                "result": "recorded",
                "evidence": {
                    "legacy_event_type": legacy.event_type,
                    "legacy_payload_keys": sorted(str(key) for key in payload),
                },
            },
        )

    if unverified_active:
        Audit.objects.using(control).get_or_create(
            action="legacy_active_projection_recorded",
            reason_code="legacy_runtime_state_unverified",
            legacy_database_id=0,
            defaults={
                "public_id": uuid.uuid4(),
                "correlation_id": uuid.uuid4(),
                "before_state": {},
                "after_state": {"runtime_state": "unknown"},
                "result": "recorded",
                "evidence": {
                    "generation_ids": sorted(unverified_active),
                    "message_code": "legacy_active_was_database_label_only",
                },
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0019_sitesetting"),
        ("vaultops", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            backfill_legacy_lifecycle,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
