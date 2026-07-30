import uuid

from django.db import models
from django.db.models import Q


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class VaultConnectionProfile(TimeStampedModel):
    class Source(models.TextChoices):
        ENVIRONMENT = "environment", "Environment"
        STORED = "stored", "Stored"
        LEGACY = "legacy", "Legacy projection"

    key = models.SlugField(max_length=80, unique=True)
    display_name = models.CharField(max_length=160)
    source = models.CharField(
        max_length=16, choices=Source.choices, default=Source.STORED
    )
    enabled = models.BooleanField(default=True)
    read_only = models.BooleanField(default=True)
    environment_locked = models.BooleanField(default=False)
    endpoint_origin = models.CharField(max_length=500, blank=True, default="")
    bucket = models.CharField(max_length=255, blank=True, default="")
    region = models.CharField(max_length=80, blank=True, default="")
    dataset_id = models.CharField(max_length=120)
    production_source_id = models.CharField(max_length=120, blank=True, default="")
    credential_alias = models.CharField(max_length=120, blank=True, default="")
    capability_evidence = models.JSONField(default=dict, blank=True)
    fingerprint = models.CharField(max_length=64, blank=True, default="")
    last_probed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["key"]


class VaultDatasetProjection(TimeStampedModel):
    profile = models.ForeignKey(
        VaultConnectionProfile,
        on_delete=models.PROTECT,
        related_name="dataset_projections",
    )
    dataset_id = models.CharField(max_length=120)
    registration_digest = models.CharField(max_length=64, blank=True, default="")
    pointer_digest = models.CharField(max_length=64, blank=True, default="")
    pointer_etag = models.CharField(max_length=255, blank=True, default="")
    authoritative_generation_id = models.CharField(
        max_length=160, blank=True, default=""
    )
    inventory_state = models.CharField(max_length=32, default="unknown")
    inventory_observed_at = models.DateTimeField(null=True, blank=True)
    state_version = models.PositiveBigIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["profile", "dataset_id"],
                name="vaultops_unique_profile_dataset",
            ),
        ]
        ordering = ["profile__key", "dataset_id"]


class ArtifactGeneration(TimeStampedModel):
    class Origin(models.TextChoices):
        VAULT_GENERATION = "vault_generation", "Vault generation"
        LOCAL_MAINTENANCE = "local_maintenance", "Local maintenance"
        LEGACY_PROJECTION = "legacy_projection", "Legacy projection"

    class VaultState(models.TextChoices):
        CANDIDATE = "candidate", "Candidate"
        AUTHORITATIVE = "authoritative", "Authoritative"
        RETIRED = "retired", "Retired"
        INVALID = "invalid", "Invalid"
        UNKNOWN = "unknown", "Unknown"
        LEGACY_READ_ONLY = "legacy_read_only", "Legacy read only"

    class RuntimeState(models.TextChoices):
        INACTIVE = "inactive", "Inactive"
        PENDING = "pending", "Pending"
        APPLYING = "applying", "Applying"
        ACTIVE = "active", "Active"
        PREVIOUS = "previous", "Previous"
        ROLLBACK_PENDING = "rollback_pending", "Rollback pending"
        ROLLED_BACK = "rolled_back", "Rolled back"
        ACTIVATION_FAILED = "activation_failed", "Activation failed"
        UNKNOWN = "unknown", "Unknown"

    class LocalPresence(models.TextChoices):
        ABSENT = "absent", "Absent"
        QUARANTINED = "quarantined", "Quarantined"
        PREPARED = "prepared", "Prepared"
        UNKNOWN = "unknown", "Unknown"

    profile = models.ForeignKey(
        VaultConnectionProfile,
        on_delete=models.PROTECT,
        related_name="generations",
    )
    origin = models.CharField(
        max_length=24,
        choices=Origin.choices,
        default=Origin.VAULT_GENERATION,
    )
    dataset_id = models.CharField(max_length=120)
    generation_id = models.CharField(max_length=160)
    manifest_digest = models.CharField(max_length=64, blank=True, default="")
    manifest = models.JSONField(default=dict, blank=True)
    vault_state = models.CharField(
        max_length=24, choices=VaultState.choices, default=VaultState.UNKNOWN
    )
    runtime_state = models.CharField(
        max_length=24, choices=RuntimeState.choices, default=RuntimeState.UNKNOWN
    )
    local_presence = models.CharField(
        max_length=16, choices=LocalPresence.choices, default=LocalPresence.UNKNOWN
    )
    deployment_id = models.CharField(max_length=120, blank=True, default="")
    source = models.CharField(max_length=80, blank=True, default="")
    lineage_job_public_id = models.UUIDField(null=True, blank=True)
    parent_generation_id = models.CharField(
        max_length=160, blank=True, default=""
    )
    parent_manifest_digest = models.CharField(
        max_length=64, blank=True, default=""
    )
    legacy_database_id = models.PositiveBigIntegerField(null=True, blank=True)
    legacy_status = models.CharField(max_length=24, blank=True, default="")
    observed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["profile", "dataset_id", "generation_id"],
                name="vaultops_unique_generation",
            ),
            models.UniqueConstraint(
                fields=["deployment_id"],
                condition=Q(runtime_state="active") & ~Q(deployment_id=""),
                name="vaultops_one_active_generation_per_deployment",
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(origin="local_maintenance")
                    | (
                        Q(lineage_job_public_id__isnull=False)
                        & ~Q(parent_generation_id="")
                        & ~Q(parent_manifest_digest="")
                    )
                ),
                name="vaultops_local_origin_has_lineage",
            ),
            models.CheckConstraint(
                condition=(
                    Q(origin="local_maintenance")
                    | (
                        Q(lineage_job_public_id__isnull=True)
                        & Q(parent_generation_id="")
                        & Q(parent_manifest_digest="")
                    )
                ),
                name="vaultops_nonlocal_origin_has_no_lineage",
            ),
            models.CheckConstraint(
                condition=(
                    ~Q(origin="local_maintenance")
                    | Q(vault_state="unknown")
                ),
                name="vaultops_local_origin_not_vault_authority",
            ),
            models.UniqueConstraint(
                fields=["lineage_job_public_id"],
                condition=Q(origin="local_maintenance"),
                name="vaultops_unique_local_maintenance_job",
            ),
        ]
        ordering = ["-created_at"]

    @property
    def status(self):
        """Read-only compatibility label for one release."""
        return self.legacy_status or self.vault_state


class ArtifactValidation(TimeStampedModel):
    class Status(models.TextChoices):
        PASSED = "passed", "Passed"
        FAILED = "failed", "Failed"
        WARNED = "warned", "Warned"
        UNKNOWN = "unknown", "Unknown"

    generation = models.ForeignKey(
        ArtifactGeneration,
        on_delete=models.CASCADE,
        related_name="validations",
    )
    validation_type = models.CharField(max_length=40)
    status = models.CharField(max_length=16, choices=Status.choices)
    manifest_digest = models.CharField(max_length=64, blank=True, default="")
    validator_version = models.CharField(max_length=80, blank=True, default="")
    reason_codes = models.JSONField(default=list, blank=True)
    evidence = models.JSONField(default=dict, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    validated_by_id = models.PositiveBigIntegerField(null=True, blank=True)
    validated_by_name = models.CharField(max_length=150, blank=True, default="")
    legacy_database_id = models.PositiveBigIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class RestoreWorkspace(TimeStampedModel):
    class State(models.TextChoices):
        ABSENT = "absent", "Absent"
        PLANNED = "planned", "Planned"
        DOWNLOADING = "downloading", "Downloading"
        DOWNLOAD_PAUSED = "download_paused", "Download paused"
        DOWNLOADED = "downloaded", "Downloaded"
        VALIDATING = "validating", "Validating"
        SANITIZING = "sanitizing", "Sanitizing"
        MIGRATION_REHEARSAL = "migration_rehearsal", "Migration rehearsal"
        ACTIVATION_READY = "activation_ready", "Activation ready"
        FAILED = "failed", "Failed"
        EXPIRED = "expired", "Expired"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    job = models.ForeignKey(
        "VaultJob",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="restore_workspaces",
    )
    generation = models.ForeignKey(
        ArtifactGeneration,
        on_delete=models.PROTECT,
        related_name="restore_workspaces",
    )
    state = models.CharField(
        max_length=24, choices=State.choices, default=State.PLANNED
    )
    manifest_digest = models.CharField(max_length=64)
    profile_fingerprint = models.CharField(
        max_length=64, blank=True, default=""
    )
    pointer_digest = models.CharField(max_length=64, blank=True, default="")
    import_idempotency_key = models.CharField(
        max_length=160, blank=True, default=""
    )
    quarantine_path = models.CharField(max_length=1000, blank=True, default="")
    runtime_path = models.CharField(max_length=1000, blank=True, default="")
    capacity_plan = models.JSONField(default=dict, blank=True)
    validation_evidence = models.JSONField(default=dict, blank=True)
    sanitization_evidence = models.JSONField(default=dict, blank=True)
    rehearsal_evidence = models.JSONField(default=dict, blank=True)
    safe_error_code = models.CharField(max_length=80, blank=True, default="")
    downloaded_objects = models.PositiveBigIntegerField(default=0)
    downloaded_bytes = models.PositiveBigIntegerField(default=0)
    checkpoint_lineage = models.UUIDField(default=uuid.uuid4, editable=False)
    prepared_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["import_idempotency_key"],
                condition=~Q(import_idempotency_key=""),
                name="vaultops_unique_workspace_import_idempotency",
            ),
        ]
        ordering = ["-created_at"]


class VaultJob(TimeStampedModel):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        CLAIMED = "claimed", "Claimed"
        RUNNING = "running", "Running"
        WAITING = "waiting", "Waiting"
        CANCELLING = "cancelling", "Cancelling"
        RETRYABLE_FAILED = "retryable_failed", "Retryable failed"
        TERMINAL_FAILED = "terminal_failed", "Terminal failed"
        STALE = "stale", "Stale"
        CANCELLED = "cancelled", "Cancelled"
        SUCCEEDED = "succeeded", "Succeeded"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    operation = models.CharField(max_length=48)
    phase = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(
        max_length=24, choices=Status.choices, default=Status.QUEUED
    )
    profile = models.ForeignKey(
        VaultConnectionProfile,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="jobs",
    )
    profile_fingerprint = models.CharField(
        max_length=64, blank=True, default=""
    )
    dataset_id = models.CharField(max_length=120, blank=True, default="")
    generation_id = models.CharField(max_length=160, blank=True, default="")
    manifest_digest = models.CharField(max_length=64, blank=True, default="")
    progress = models.JSONField(default=dict, blank=True)
    claim_token_hash = models.CharField(max_length=64, blank=True, default="")
    claimed_by = models.CharField(max_length=160, blank=True, default="")
    fencing_epoch = models.PositiveBigIntegerField(default=0)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    correlation_id = models.UUIDField(default=uuid.uuid4, editable=False)
    idempotency_key = models.CharField(max_length=160)
    cancellation_requested_at = models.DateTimeField(null=True, blank=True)
    safe_error_code = models.CharField(max_length=80, blank=True, default="")
    retry_count = models.PositiveIntegerField(default=0)
    state_version = models.PositiveBigIntegerField(default=1)
    requested_by_id = models.PositiveBigIntegerField(null=True, blank=True)
    requested_by_name = models.CharField(max_length=150, blank=True, default="")
    legacy_database_id = models.PositiveBigIntegerField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["operation", "idempotency_key"],
                name="vaultops_unique_operation_idempotency",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["operation", "created_at"]),
            models.Index(fields=["heartbeat_at"]),
        ]
        ordering = ["-created_at"]


class VaultJobStep(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        WAITING = "waiting", "Waiting"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    job = models.ForeignKey(VaultJob, on_delete=models.CASCADE, related_name="steps")
    phase = models.CharField(max_length=64)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING
    )
    checkpoint = models.JSONField(default=dict, blank=True)
    completed_objects = models.PositiveBigIntegerField(default=0)
    completed_bytes = models.PositiveBigIntegerField(default=0)
    attempts = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["job", "phase"], name="vaultops_unique_job_phase"
            ),
        ]
        ordering = ["created_at"]


class VaultJobRetryRequest(TimeStampedModel):
    """Durable idempotency receipt for one operator retry request."""

    class Mode(models.TextChoices):
        FRESH_SNAPSHOT = "fresh_snapshot", "Fresh snapshot"
        CHECKPOINT_RESUME = "checkpoint_resume", "Checkpoint resume"
        OPERATION_RETRY = "operation_retry", "Operation retry"

    job = models.ForeignKey(
        VaultJob,
        on_delete=models.PROTECT,
        related_name="retry_requests",
    )
    idempotency_key = models.CharField(max_length=160)
    requested_state_version = models.PositiveBigIntegerField()
    resulting_state_version = models.PositiveBigIntegerField()
    resulting_retry_count = models.PositiveIntegerField()
    mode = models.CharField(max_length=24, choices=Mode.choices)
    actor_id = models.PositiveBigIntegerField(null=True, blank=True)
    actor_name = models.CharField(max_length=150, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["job", "idempotency_key"],
                name="vaultops_unique_job_retry_request",
            ),
        ]
        ordering = ["created_at"]


class SourceMutationState(TimeStampedModel):
    class BarrierState(models.TextChoices):
        OPEN = "open", "Open"
        REQUESTED = "requested", "Requested"
        ACTIVE = "active", "Active"

    deployment_id = models.CharField(max_length=120, unique=True)
    current_epoch = models.PositiveBigIntegerField(default=0)
    active_mutations = models.PositiveIntegerField(default=0)
    barrier_state = models.CharField(
        max_length=16, choices=BarrierState.choices, default=BarrierState.OPEN
    )
    barrier_owner_job = models.UUIDField(null=True, blank=True)
    barrier_requested_at = models.DateTimeField(null=True, blank=True)
    barrier_activated_at = models.DateTimeField(null=True, blank=True)
    last_mutation_at = models.DateTimeField(null=True, blank=True)


class MutationJournalEntry(models.Model):
    deployment_id = models.CharField(max_length=120)
    epoch = models.PositiveBigIntegerField()
    category = models.CharField(max_length=40)
    relative_path = models.CharField(max_length=1000, blank=True, default="")
    operation = models.CharField(max_length=40)
    correlation_id = models.UUIDField(default=uuid.uuid4)
    observed_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["deployment_id", "epoch"],
                name="vaultops_unique_mutation_epoch",
            ),
        ]
        indexes = [
            models.Index(fields=["deployment_id", "epoch"]),
            models.Index(fields=["deployment_id", "-observed_at"]),
        ]
        ordering = ["epoch"]


class SourceSnapshot(TimeStampedModel):
    class State(models.TextChoices):
        PLANNED = "planned", "Planned"
        COPYING = "copying", "Copying"
        BARRIER = "barrier", "Barrier"
        FINALIZED = "finalized", "Finalized"
        FAILED = "failed", "Failed"

    class CleanupState(models.TextChoices):
        NONE = "none", "No cleanup"
        PENDING = "pending", "Cleanup pending"
        RECLAIMING = "reclaiming", "Cleanup in progress"
        COMPLETED = "completed", "Cleanup completed"
        FAILED = "failed", "Cleanup failed"
        QUARANTINED = "quarantined", "Cleanup quarantined"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    job = models.ForeignKey(
        VaultJob,
        on_delete=models.PROTECT,
        related_name="source_snapshots",
    )
    deployment_id = models.CharField(max_length=120)
    state = models.CharField(
        max_length=16, choices=State.choices, default=State.PLANNED
    )
    initial_epoch = models.PositiveBigIntegerField(default=0)
    included_epoch = models.PositiveBigIntegerField(default=0)
    snapshot_digest = models.CharField(max_length=64, blank=True, default="")
    workspace_path = models.CharField(max_length=1000, blank=True, default="")
    file_count = models.PositiveBigIntegerField(default=0)
    byte_count = models.PositiveBigIntegerField(default=0)
    evidence = models.JSONField(default=dict, blank=True)
    safe_error_code = models.CharField(max_length=80, blank=True, default="")
    finalized_at = models.DateTimeField(null=True, blank=True)
    cleanup_state = models.CharField(
        max_length=16, choices=CleanupState.choices, default=CleanupState.NONE
    )
    cleanup_path = models.CharField(max_length=1000, blank=True, default="")
    cleanup_not_before = models.DateTimeField(null=True, blank=True)
    cleanup_attempts = models.PositiveIntegerField(default=0)
    cleanup_error_code = models.CharField(
        max_length=80, blank=True, default=""
    )

    class Meta:
        ordering = ["-created_at"]


class SyncPolicy(TimeStampedModel):
    class Mode(models.TextChoices):
        DISABLED = "disabled", "Disabled"
        MANUAL = "manual", "Manual"
        SCHEDULED = "scheduled", "Scheduled"
        CONTINUOUS_COALESCED = "continuous_coalesced", "Continuous coalesced"

    class PromotionMode(models.TextChoices):
        PUBLISH_ONLY = "publish_only", "Publish only"
        MANUAL = "manual", "Manual"
        AUTO_AFTER_VALIDATION = "auto_after_validation", "Auto after validation"

    profile = models.ForeignKey(
        VaultConnectionProfile,
        on_delete=models.PROTECT,
        related_name="sync_policies",
    )
    dataset_id = models.CharField(max_length=120)
    mode = models.CharField(
        max_length=24, choices=Mode.choices, default=Mode.DISABLED
    )
    promotion_mode = models.CharField(
        max_length=24,
        choices=PromotionMode.choices,
        default=PromotionMode.MANUAL,
    )
    quiet_period_seconds = models.PositiveIntegerField(default=120)
    interval_seconds = models.PositiveIntegerField(default=900)
    max_lag_seconds = models.PositiveIntegerField(default=3600)
    environment_locked = models.BooleanField(default=False)
    pending_epoch = models.PositiveBigIntegerField(default=0)
    last_completed_epoch = models.PositiveBigIntegerField(default=0)
    last_evaluated_at = models.DateTimeField(null=True, blank=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    warning_code = models.CharField(max_length=80, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["profile", "dataset_id"],
                name="vaultops_unique_sync_policy",
            ),
        ]


class ActivationIntent(TimeStampedModel):
    class State(models.TextChoices):
        PENDING = "pending", "Pending"
        APPLYING = "applying", "Applying"
        COMMITTED = "committed", "Committed"
        ROLLED_BACK = "rolled_back", "Rolled back"
        FAILED = "failed", "Failed"
        EXPIRED = "expired", "Expired"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    workspace = models.ForeignKey(
        RestoreWorkspace,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="activation_intents",
    )
    deployment_id = models.CharField(max_length=120)
    target_generation_id = models.CharField(max_length=160)
    previous_generation_id = models.CharField(max_length=160)
    manifest_digest = models.CharField(max_length=64)
    intent_digest = models.CharField(max_length=64, unique=True)
    idempotency_key = models.CharField(max_length=160, blank=True, default="")
    request_state_digest = models.CharField(
        max_length=64, blank=True, default=""
    )
    state = models.CharField(
        max_length=16, choices=State.choices, default=State.PENDING
    )
    checkpoint = models.JSONField(default=dict, blank=True)
    state_version = models.PositiveBigIntegerField(default=1)
    actor_id = models.PositiveBigIntegerField(null=True, blank=True)
    actor_name = models.CharField(max_length=150, blank=True, default="")
    safe_error_code = models.CharField(max_length=80, blank=True, default="")
    applied_at = models.DateTimeField(null=True, blank=True)
    committed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["deployment_id", "idempotency_key"],
                condition=~Q(idempotency_key=""),
                name="vaultops_unique_activation_request",
            ),
        ]
        ordering = ["-created_at"]


class RuntimePointerObservation(models.Model):
    deployment_id = models.CharField(max_length=120)
    active_generation_id = models.CharField(max_length=160, blank=True, default="")
    previous_generation_id = models.CharField(max_length=160, blank=True, default="")
    pointer_digest = models.CharField(max_length=64, blank=True, default="")
    process_identity = models.JSONField(default=dict, blank=True)
    readiness_evidence = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=24, default="unknown")
    observed_at = models.DateTimeField()

    class Meta:
        indexes = [
            models.Index(fields=["deployment_id", "-observed_at"]),
        ]
        ordering = ["-observed_at"]


class RetentionHold(TimeStampedModel):
    generation = models.ForeignKey(
        ArtifactGeneration,
        on_delete=models.PROTECT,
        related_name="retention_holds",
    )
    reason_code = models.CharField(max_length=80)
    owner_reference = models.CharField(max_length=160)
    notes = models.TextField(blank=True, default="")
    idempotency_key = models.CharField(max_length=160, blank=True, default="")
    expires_at = models.DateTimeField(null=True, blank=True)
    released_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["generation", "released_at"])]
        constraints = [
            models.UniqueConstraint(
                fields=["generation", "idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="vaultops_hold_generation_idempotency_uniq",
            ),
        ]


class GarbageCollectionPlan(TimeStampedModel):
    class State(models.TextChoices):
        DRAFT = "draft", "Draft"
        READY = "ready", "Ready"
        EXPIRED = "expired", "Expired"
        EXECUTING = "executing", "Executing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    profile = models.ForeignKey(
        VaultConnectionProfile,
        on_delete=models.PROTECT,
        related_name="gc_plans",
    )
    dataset_id = models.CharField(max_length=120)
    inventory_version = models.CharField(max_length=128)
    pointer_version = models.CharField(max_length=128)
    candidates = models.JSONField(default=list, blank=True)
    estimates = models.JSONField(default=dict, blank=True)
    plan_digest = models.CharField(max_length=64, unique=True)
    state = models.CharField(
        max_length=16, choices=State.choices, default=State.DRAFT
    )
    expires_at = models.DateTimeField()

    class Meta:
        ordering = ["-created_at"]


class ConfirmationChallenge(models.Model):
    """One-use typed confirmation bound to actor, action, and observed state."""

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    actor_id = models.PositiveBigIntegerField()
    action = models.CharField(max_length=80)
    target = models.CharField(max_length=200)
    state_digest = models.CharField(max_length=64)
    phrase_salt = models.CharField(max_length=64)
    phrase_digest = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["actor_id", "action", "expires_at"],
                name="vaultops_co_actor_i_c24ad8_idx",
            ),
        ]
        ordering = ["-created_at"]


class AppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise TypeError("VaultAuditEvent records are append-only")

    def delete(self):
        raise TypeError("VaultAuditEvent records are append-only")


class VaultAuditEvent(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    actor_id = models.PositiveBigIntegerField(null=True, blank=True)
    actor_name = models.CharField(max_length=150, blank=True, default="")
    correlation_id = models.UUIDField(default=uuid.uuid4)
    job_public_id = models.UUIDField(null=True, blank=True)
    action = models.CharField(max_length=80)
    reason_code = models.CharField(max_length=80, blank=True, default="")
    before_state = models.JSONField(default=dict, blank=True)
    after_state = models.JSONField(default=dict, blank=True)
    confirmation_digest = models.CharField(max_length=64, blank=True, default="")
    result = models.CharField(max_length=32)
    safe_error_code = models.CharField(max_length=80, blank=True, default="")
    evidence = models.JSONField(default=dict, blank=True)
    legacy_database_id = models.PositiveBigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise TypeError("VaultAuditEvent records are append-only")
        kwargs["force_insert"] = True
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise TypeError("VaultAuditEvent records are append-only")
