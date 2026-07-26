import hashlib
import json
import uuid
from collections import defaultdict
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.namespace import KeyBuilder
from vaultops.models import (
    ArtifactGeneration,
    GarbageCollectionPlan,
    RestoreWorkspace,
    RetentionHold,
    VaultDatasetProjection,
    VaultJob,
)
from vaultops.services.audit import append_event
from vaultops.services.lifecycle import transition_generation_vault_state


CONTROL_DB = "control"
ACTIVE_JOB_STATES = {
    VaultJob.Status.QUEUED,
    VaultJob.Status.CLAIMED,
    VaultJob.Status.RUNNING,
    VaultJob.Status.WAITING,
    VaultJob.Status.CANCELLING,
}
PROTECTED_RUNTIME_STATES = {
    ArtifactGeneration.RuntimeState.PENDING,
    ArtifactGeneration.RuntimeState.APPLYING,
    ArtifactGeneration.RuntimeState.ACTIVE,
    ArtifactGeneration.RuntimeState.PREVIOUS,
    ArtifactGeneration.RuntimeState.ROLLBACK_PENDING,
}
MAX_GC_CANDIDATES = 500


class RetentionError(RuntimeError):
    status_code = 409

    def __init__(self, reason_code, *, status_code=None):
        self.reason_code = reason_code
        if status_code is not None:
            self.status_code = status_code
        super().__init__(reason_code)


def _canonical_digest(payload):
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _active_holds(generation, *, now=None):
    now = now or timezone.now()
    return RetentionHold.objects.filter(
        generation=generation,
        released_at__isnull=True,
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))


def generation_protection_reasons(generation, *, now=None):
    now = now or timezone.now()
    reasons = set()
    projection = VaultDatasetProjection.objects.filter(
        profile=generation.profile,
        dataset_id=generation.dataset_id,
    ).first()
    if (
        generation.vault_state
        == ArtifactGeneration.VaultState.AUTHORITATIVE
        or (
            projection
            and projection.authoritative_generation_id
            == generation.generation_id
        )
    ):
        reasons.add("generation_authoritative")
    if generation.runtime_state in PROTECTED_RUNTIME_STATES:
        reasons.add("generation_runtime_referenced")
    if generation.restore_workspaces.exclude(
        state=RestoreWorkspace.State.EXPIRED
    ).exists():
        reasons.add("workspace_referenced")
    if VaultJob.objects.filter(
        profile=generation.profile,
        dataset_id=generation.dataset_id,
        generation_id=generation.generation_id,
        status__in=ACTIVE_JOB_STATES,
    ).exists():
        reasons.add("generation_job_in_progress")
    if _active_holds(generation, now=now).exists():
        reasons.add("retention_hold_active")
    return sorted(reasons)


def retire_generation(
    generation,
    *,
    actor_id=None,
    actor_name="",
    correlation_id=None,
    reason_code="operator_retirement",
):
    correlation_id = correlation_id or uuid.uuid4()
    with transaction.atomic(using=CONTROL_DB):
        generation = (
            ArtifactGeneration.objects.select_for_update()
            .select_related("profile")
            .get(pk=generation.pk)
        )
        blocking = [
            reason
            for reason in generation_protection_reasons(generation)
            if reason
            in {
                "generation_authoritative",
                "generation_runtime_referenced",
                "generation_job_in_progress",
            }
        ]
        if blocking:
            raise RetentionError(blocking[0])
        return transition_generation_vault_state(
            generation,
            ArtifactGeneration.VaultState.RETIRED,
            correlation_id=correlation_id,
            actor_id=actor_id,
            actor_name=actor_name,
            reason_code=reason_code,
        )


def unretire_generation(
    generation,
    *,
    actor_id=None,
    actor_name="",
    correlation_id=None,
    reason_code="operator_unretirement",
):
    correlation_id = correlation_id or uuid.uuid4()
    with transaction.atomic(using=CONTROL_DB):
        generation = (
            ArtifactGeneration.objects.select_for_update()
            .select_related("profile")
            .get(pk=generation.pk)
        )
        if generation.vault_state != ArtifactGeneration.VaultState.RETIRED:
            raise RetentionError("generation_not_retired")
        return transition_generation_vault_state(
            generation,
            ArtifactGeneration.VaultState.CANDIDATE,
            correlation_id=correlation_id,
            actor_id=actor_id,
            actor_name=actor_name,
            reason_code=reason_code,
        )


def create_retention_hold(
    generation,
    *,
    reason_code,
    owner_reference,
    notes="",
    expires_at=None,
    actor_id=None,
    actor_name="",
):
    reason_code = str(reason_code).strip()
    owner_reference = str(owner_reference).strip()
    if not reason_code or not owner_reference:
        raise RetentionError("retention_hold_fields_required", status_code=400)
    if expires_at and expires_at <= timezone.now():
        raise RetentionError("retention_hold_expiry_invalid", status_code=400)
    correlation_id = uuid.uuid4()
    with transaction.atomic(using=CONTROL_DB):
        hold = RetentionHold.objects.create(
            generation=generation,
            reason_code=reason_code[:80],
            owner_reference=owner_reference[:160],
            notes=str(notes)[:2000],
            expires_at=expires_at,
        )
        append_event(
            action="retention_hold_created",
            result="succeeded",
            correlation_id=correlation_id,
            actor_id=actor_id,
            actor_name=actor_name,
            reason_code=hold.reason_code,
            after_state={
                "hold_id": hold.pk,
                "generation_id": generation.generation_id,
                "expires_at": hold.expires_at,
            },
            evidence={"owner_reference": hold.owner_reference},
        )
        return hold


def release_retention_hold(
    hold,
    *,
    actor_id=None,
    actor_name="",
    reason_code="operator_release",
):
    correlation_id = uuid.uuid4()
    with transaction.atomic(using=CONTROL_DB):
        hold = (
            RetentionHold.objects.select_for_update()
            .select_related("generation")
            .get(pk=hold.pk)
        )
        if hold.released_at is not None:
            return hold
        hold.released_at = timezone.now()
        hold.save(update_fields=["released_at", "updated_at"])
        append_event(
            action="retention_hold_released",
            result="succeeded",
            correlation_id=correlation_id,
            actor_id=actor_id,
            actor_name=actor_name,
            reason_code=reason_code,
            before_state={
                "hold_id": hold.pk,
                "generation_id": hold.generation.generation_id,
                "released_at": None,
            },
            after_state={"released_at": hold.released_at},
        )
        return hold


def _verified_projection(profile, dataset_id, *, now):
    projection = VaultDatasetProjection.objects.filter(
        profile=profile,
        dataset_id=dataset_id,
    ).first()
    if (
        projection is None
        or projection.inventory_state != "verified"
        or projection.inventory_observed_at is None
    ):
        raise RetentionError("gc_inventory_unverified")
    max_age = timedelta(seconds=settings.VAULT_INVENTORY_CACHE_SECONDS)
    if projection.inventory_observed_at < now - max_age:
        raise RetentionError("gc_inventory_stale")
    return projection


def _manifest_objects(generation):
    files = generation.manifest.get("files", [])
    if not isinstance(files, list):
        raise RetentionError("gc_reference_graph_invalid")
    records = {}
    for entry in files:
        if not isinstance(entry, dict):
            raise RetentionError("gc_reference_graph_invalid")
        key = entry.get("object_key")
        digest = entry.get("sha256")
        size = entry.get("bytes")
        if (
            not isinstance(key, str)
            or not key
            or not isinstance(digest, str)
            or len(digest) != 64
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
        ):
            raise RetentionError("gc_reference_graph_invalid")
        record = {"sha256": digest, "bytes": size}
        if key in records and records[key] != record:
            raise RetentionError("gc_reference_graph_invalid")
        records[key] = record
    return records


def create_gc_plan(
    profile,
    *,
    dataset_id,
    actor_id=None,
    actor_name="",
    now=None,
):
    now = now or timezone.now()
    with transaction.atomic(using=CONTROL_DB):
        projection = _verified_projection(profile, dataset_id, now=now)
        existing = GarbageCollectionPlan.objects.select_for_update().filter(
            profile=profile,
            dataset_id=dataset_id,
            state__in=[
                GarbageCollectionPlan.State.DRAFT,
                GarbageCollectionPlan.State.READY,
                GarbageCollectionPlan.State.EXECUTING,
            ],
            expires_at__gt=now,
        )
        if existing.exists():
            raise RetentionError("gc_plan_in_progress")

        generations = list(
            ArtifactGeneration.objects.select_for_update()
            .filter(profile=profile, dataset_id=dataset_id)
            .order_by("generation_id")
        )
        if not generations:
            raise RetentionError("gc_inventory_empty")
        grace_cutoff = now - timedelta(days=settings.VAULT_GC_GRACE_DAYS)
        eligible = []
        blocked = []
        for generation in generations:
            if generation.vault_state != ArtifactGeneration.VaultState.RETIRED:
                continue
            reasons = generation_protection_reasons(generation, now=now)
            if generation.updated_at > grace_cutoff:
                reasons.append("gc_grace_period_active")
            if reasons:
                blocked.append(
                    {
                        "generation_id": generation.generation_id,
                        "reason_codes": sorted(set(reasons)),
                    }
                )
            else:
                eligible.append(generation)
        if not eligible:
            raise RetentionError("gc_no_eligible_candidates")
        if len(eligible) > MAX_GC_CANDIDATES:
            raise RetentionError("gc_candidate_limit_exceeded")

        references = defaultdict(set)
        object_metadata = {}
        generation_objects = {}
        for generation in generations:
            records = _manifest_objects(generation)
            generation_objects[generation.generation_id] = records
            for key, metadata in records.items():
                references[key].add(generation.generation_id)
                if key in object_metadata and object_metadata[key] != metadata:
                    raise RetentionError("gc_reference_graph_invalid")
                object_metadata[key] = metadata

        candidate_ids = {item.generation_id for item in eligible}
        candidates = []
        exclusive_keys = set()
        shared_keys = set()
        for generation in eligible:
            deletable = []
            shared = []
            for key in sorted(generation_objects[generation.generation_id]):
                retained_references = references[key] - candidate_ids
                if retained_references:
                    shared.append(key)
                    shared_keys.add(key)
                else:
                    deletable.append(key)
                    exclusive_keys.add(key)
            candidates.append(
                {
                    "generation_id": generation.generation_id,
                    "manifest_digest": generation.manifest_digest,
                    "manifest_object_key": KeyBuilder(
                        dataset_id
                    ).generation_manifest(generation.generation_id),
                    "exclusive_object_keys": deletable,
                    "shared_object_keys": shared,
                }
            )

        estimates = {
            "candidate_generations": len(candidates),
            "blocked_generations": blocked,
            "exclusive_objects": len(exclusive_keys),
            "exclusive_bytes": sum(
                object_metadata[key]["bytes"] for key in exclusive_keys
            ),
            "shared_objects": len(shared_keys),
            "shared_bytes": sum(
                object_metadata[key]["bytes"] for key in shared_keys
            ),
            "manifest_objects": len(candidates),
            "deletion_enabled": False,
        }
        inventory_version = _canonical_digest(
            {
                "registration_digest": projection.registration_digest,
                "state_version": projection.state_version,
                "inventory_observed_at": projection.inventory_observed_at,
            }
        )
        digest_payload = {
            "profile_fingerprint": profile.fingerprint,
            "dataset_id": dataset_id,
            "inventory_version": inventory_version,
            "pointer_version": projection.pointer_digest,
            "candidates": candidates,
            "estimates": estimates,
        }
        plan_digest = _canonical_digest(digest_payload)
        plan, _ = GarbageCollectionPlan.objects.get_or_create(
            plan_digest=plan_digest,
            defaults={
                "profile": profile,
                "dataset_id": dataset_id,
                "inventory_version": inventory_version,
                "pointer_version": projection.pointer_digest,
                "candidates": candidates,
                "estimates": estimates,
                "state": GarbageCollectionPlan.State.READY,
                "expires_at": now
                + timedelta(seconds=settings.VAULT_INVENTORY_CACHE_SECONDS),
            },
        )
        append_event(
            action="gc_plan_created",
            result="succeeded",
            correlation_id=uuid.uuid4(),
            actor_id=actor_id,
            actor_name=actor_name,
            reason_code="gc_dry_run",
            after_state={
                "plan_id": str(plan.public_id),
                "plan_digest": plan.plan_digest,
                "state": plan.state,
            },
            evidence=estimates,
        )
        return plan


def execute_gc_plan(plan, *, actor_id=None, actor_name=""):
    if not settings.VAULT_GC_ENABLED:
        raise RetentionError("gc_execution_disabled", status_code=409)
    raise RetentionError("gc_execution_unavailable", status_code=409)
