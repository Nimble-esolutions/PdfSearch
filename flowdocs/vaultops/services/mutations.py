import contextvars
import time
import uuid
from contextlib import contextmanager

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from vaultops.models import MutationJournalEntry, SourceMutationState


CONTROL_DB = "control"
_scope_depth = contextvars.ContextVar("vaultops_mutation_scope_depth", default=0)


class SnapshotBarrierActive(RuntimeError):
    reason_code = "snapshot_barrier_active"

    def __init__(self, reason_code=None):
        self.reason_code = reason_code or self.reason_code
        super().__init__(self.reason_code)


class ConsistentSnapshotUnproven(RuntimeError):
    reason_code = "consistent_snapshot_unproven"

    def __init__(self, reason_code=None):
        self.reason_code = reason_code or self.reason_code
        super().__init__(self.reason_code)


class BarrierOwnershipLost(RuntimeError):
    reason_code = "snapshot_barrier_ownership_lost"

    def __init__(self, reason_code=None):
        self.reason_code = reason_code or self.reason_code
        super().__init__(self.reason_code)


def tracking_enabled():
    return bool(getattr(settings, "VAULT_MUTATION_TRACKING_ENABLED", False))


def deployment_id():
    identity = getattr(settings, "ENV_IDENTITY", None)
    return (
        getattr(identity, "deployment_id", "")
        or getattr(settings, "DEPLOYMENT_ID", "")
        or "local"
    )


def get_mutation_state(*, source_deployment=None):
    source_deployment = source_deployment or deployment_id()
    state, _ = SourceMutationState.objects.get_or_create(
        deployment_id=source_deployment
    )
    return state


def current_epoch(*, source_deployment=None):
    return get_mutation_state(source_deployment=source_deployment).current_epoch


@contextmanager
def mutation_scope(
    *,
    category,
    relative_path="",
    operation="write",
    correlation_id=None,
    source_deployment=None,
):
    """Fence one source mutation and record a durable coalescing epoch."""
    if not tracking_enabled():
        yield
        return

    depth = _scope_depth.get()
    if depth:
        token = _scope_depth.set(depth + 1)
        try:
            yield
        finally:
            _scope_depth.reset(token)
        return

    source_deployment = source_deployment or deployment_id()
    correlation_id = correlation_id or uuid.uuid4()
    with transaction.atomic(using=CONTROL_DB):
        state, _ = SourceMutationState.objects.select_for_update().get_or_create(
            deployment_id=source_deployment
        )
        if state.barrier_state != SourceMutationState.BarrierState.OPEN:
            raise SnapshotBarrierActive("snapshot_barrier_active")
        state.active_mutations += 1
        state.save(update_fields=["active_mutations", "updated_at"])

    token = _scope_depth.set(1)
    try:
        yield
    finally:
        _scope_depth.reset(token)
        now = timezone.now()
        with transaction.atomic(using=CONTROL_DB):
            state = SourceMutationState.objects.select_for_update().get(
                deployment_id=source_deployment
            )
            state.active_mutations = max(0, state.active_mutations - 1)
            state.current_epoch += 1
            state.last_mutation_at = now
            state.save(
                update_fields=[
                    "active_mutations",
                    "current_epoch",
                    "last_mutation_at",
                    "updated_at",
                ]
            )
            MutationJournalEntry.objects.create(
                deployment_id=source_deployment,
                epoch=state.current_epoch,
                category=category,
                relative_path=relative_path,
                operation=operation,
                correlation_id=correlation_id,
                observed_at=now,
            )


def request_barrier(
    *,
    owner_job_id,
    source_deployment=None,
    timeout_seconds=None,
    poll_seconds=0.1,
):
    """Stop new mutations, drain active scopes, and acquire the barrier."""
    if not tracking_enabled():
        raise ConsistentSnapshotUnproven("mutation_tracking_disabled")
    source_deployment = source_deployment or deployment_id()
    timeout_seconds = timeout_seconds or getattr(
        settings, "VAULT_SNAPSHOT_BARRIER_TIMEOUT_SECONDS", 30
    )
    now = timezone.now()
    with transaction.atomic(using=CONTROL_DB):
        state, _ = SourceMutationState.objects.select_for_update().get_or_create(
            deployment_id=source_deployment
        )
        if (
            state.barrier_state != SourceMutationState.BarrierState.OPEN
            and state.barrier_owner_job != owner_job_id
        ):
            raise SnapshotBarrierActive("source_snapshot_in_progress")
        state.barrier_state = SourceMutationState.BarrierState.REQUESTED
        state.barrier_owner_job = owner_job_id
        state.barrier_requested_at = now
        state.save(
            update_fields=[
                "barrier_state",
                "barrier_owner_job",
                "barrier_requested_at",
                "updated_at",
            ]
        )

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        with transaction.atomic(using=CONTROL_DB):
            state = SourceMutationState.objects.select_for_update().get(
                deployment_id=source_deployment
            )
            if state.barrier_owner_job != owner_job_id:
                raise BarrierOwnershipLost("snapshot_barrier_ownership_lost")
            if state.active_mutations == 0:
                state.barrier_state = SourceMutationState.BarrierState.ACTIVE
                state.barrier_activated_at = timezone.now()
                state.save(
                    update_fields=[
                        "barrier_state",
                        "barrier_activated_at",
                        "updated_at",
                    ]
                )
                return state
        time.sleep(max(0.01, poll_seconds))

    release_barrier(
        owner_job_id=owner_job_id,
        source_deployment=source_deployment,
        tolerate_lost=True,
    )
    raise ConsistentSnapshotUnproven("mutation_barrier_drain_timeout")


def assert_barrier_owner(*, owner_job_id, source_deployment=None):
    source_deployment = source_deployment or deployment_id()
    state = SourceMutationState.objects.get(deployment_id=source_deployment)
    if (
        state.barrier_state != SourceMutationState.BarrierState.ACTIVE
        or state.barrier_owner_job != owner_job_id
        or state.active_mutations
    ):
        raise BarrierOwnershipLost("snapshot_barrier_ownership_lost")
    return state


def release_barrier(
    *,
    owner_job_id,
    source_deployment=None,
    tolerate_lost=False,
):
    source_deployment = source_deployment or deployment_id()
    with transaction.atomic(using=CONTROL_DB):
        state = SourceMutationState.objects.select_for_update().get(
            deployment_id=source_deployment
        )
        if state.barrier_owner_job != owner_job_id:
            if tolerate_lost:
                return False
            raise BarrierOwnershipLost("snapshot_barrier_ownership_lost")
        state.barrier_state = SourceMutationState.BarrierState.OPEN
        state.barrier_owner_job = None
        state.barrier_requested_at = None
        state.barrier_activated_at = None
        state.save(
            update_fields=[
                "barrier_state",
                "barrier_owner_job",
                "barrier_requested_at",
                "barrier_activated_at",
                "updated_at",
            ]
        )
        return True


def journal_since(epoch, *, source_deployment=None):
    source_deployment = source_deployment or deployment_id()
    return list(
        MutationJournalEntry.objects.filter(
            deployment_id=source_deployment,
            epoch__gt=epoch,
        ).values(
            "epoch",
            "category",
            "relative_path",
            "operation",
            "observed_at",
        )
    )
