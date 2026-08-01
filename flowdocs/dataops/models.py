"""Small, append-friendly models for the Data Operations UI.

The control database is disposable.  Payloads and recovery points live in
object storage; JSON fields contain observations/checkpoints only and never
credentials or opaque downloaded archives.
"""

import uuid

from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class DataProfile(TimeStampedModel):
    class Role(models.TextChoices):
        BACKUP = "backup", "Backup"
        RESTORE = "restore", "Restore"
        BOTH = "both", "Backup and restore"

    class Source(models.TextChoices):
        ENVIRONMENT = "environment", "Environment"
        STORED = "stored", "Stored fallback"
        DEFAULT = "default", "Safe default"

    key = models.SlugField(max_length=80, unique=True)
    display_name = models.CharField(max_length=160)
    role = models.CharField(max_length=8, choices=Role.choices, default=Role.BOTH)
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.STORED)
    enabled = models.BooleanField(default=True)
    environment_locked = models.BooleanField(default=False)
    endpoint = models.CharField(max_length=500, blank=True, default="")
    bucket = models.CharField(max_length=255)
    region = models.CharField(max_length=80, blank=True, default="")
    dataset_id = models.CharField(max_length=120)
    source_id = models.CharField(max_length=120, blank=True, default="")
    namespace = models.CharField(max_length=200, blank=True, default="")
    prefix = models.CharField(max_length=200, blank=True, default="")
    credential_prefix = models.CharField(max_length=120, blank=True, default="")
    credential_ref = models.CharField(max_length=120, blank=True, default="")
    fingerprint = models.CharField(max_length=64, blank=True, default="")
    last_observed_at = models.DateTimeField(null=True, blank=True)
    observation = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["key"]
        indexes = [models.Index(fields=["role", "enabled"])]


class DataCredential(TimeStampedModel):
    """Optional encrypted fallback credentials.

    ``secret_ciphertext`` and ``nonce`` are opaque base64 strings.  They are
    intentionally excluded from serializers, backups and admin list views.
    """

    profile = models.OneToOneField(DataProfile, on_delete=models.CASCADE, related_name="credential")
    access_key_ciphertext = models.TextField(blank=True, default="")
    secret_ciphertext = models.TextField(blank=True, default="")
    nonce = models.CharField(max_length=64, blank=True, default="")
    key_version = models.PositiveSmallIntegerField(default=1)
    enabled = models.BooleanField(default=False)
    last_rotated_at = models.DateTimeField(null=True, blank=True)


class DataOperation(TimeStampedModel):
    class Kind(models.TextChoices):
        HEALTH_REFRESH = "health_refresh", "Health refresh"
        BACKUP = "backup", "Backup"
        RESTORE = "restore", "Restore"
        REINDEX = "reindex", "Reindex"
        AUTO_HEAL = "auto_heal", "Automatic recovery"

    class State(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"
        WAITING_APPROVAL = "waiting_approval", "Waiting for approval"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    kind = models.CharField(max_length=24, choices=Kind.choices)
    state = models.CharField(max_length=20, choices=State.choices, default=State.QUEUED)
    profile_key = models.SlugField(max_length=80, blank=True, default="")
    source_profile_key = models.SlugField(max_length=80, blank=True, default="")
    destination_profile_key = models.SlugField(max_length=80, blank=True, default="")
    release_id = models.CharField(max_length=160, blank=True, default="")
    pipeline_stage = models.CharField(max_length=32, blank=True, default="preflight")
    request_id = models.CharField(max_length=160, blank=True, default="")
    idempotency_key = models.CharField(max_length=160, blank=True, default="")
    checkpoint = models.JSONField(default=dict, blank=True)
    result = models.JSONField(default=dict, blank=True)
    error_code = models.CharField(max_length=80, blank=True, default="")
    error_detail = models.TextField(blank=True, default="")
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    attempt = models.PositiveSmallIntegerField(default=0)
    lease_token = models.CharField(max_length=64, blank=True, default="")
    lease_expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["kind", "idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="dataops_unique_operation_idempotency",
            )
        ]
        indexes = [
            models.Index(fields=["state", "kind"]),
            models.Index(fields=["state", "lease_expires_at"], name="dataops_op_state_lease_idx"),
        ]


class RecoveryPoint(TimeStampedModel):
    class State(models.TextChoices):
        DISCOVERED = "discovered", "Discovered"
        VERIFIED = "verified", "Verified"
        QUARANTINED = "quarantined", "Quarantined"
        INVALID = "invalid", "Invalid"

    profile_key = models.SlugField(max_length=80)
    dataset_id = models.CharField(max_length=120)
    release_id = models.CharField(max_length=160)
    format_version = models.PositiveSmallIntegerField(default=1)
    prefix = models.CharField(max_length=500)
    manifest_digest = models.CharField(max_length=64)
    state = models.CharField(max_length=16, choices=State.choices, default=State.DISCOVERED)
    counts = models.JSONField(default=dict, blank=True)
    identity = models.JSONField(default=dict, blank=True)
    evidence = models.JSONField(default=dict, blank=True)
    observed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["profile_key", "dataset_id", "release_id"],
                name="dataops_unique_recovery_point",
            )
        ]
        ordering = ["-created_at"]


class RestoreCandidate(TimeStampedModel):
    class State(models.TextChoices):
        QUARANTINED = "quarantined", "Quarantined"
        VERIFIED = "verified", "Verified"
        READY = "ready", "Ready for activation"
        ACTIVATED = "activated", "Activated"
        REJECTED = "rejected", "Rejected"

    recovery_point = models.ForeignKey(RecoveryPoint, on_delete=models.PROTECT, related_name="candidates")
    operation = models.OneToOneField(DataOperation, on_delete=models.PROTECT, related_name="restore_candidate")
    environment = models.CharField(max_length=32, default="stage")
    workspace = models.CharField(max_length=500)
    state = models.CharField(max_length=16, choices=State.choices, default=State.QUARANTINED)
    manifest_digest = models.CharField(max_length=64)
    evidence = models.JSONField(default=dict, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)


class DataOpsAuditEvent(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    actor_id = models.PositiveBigIntegerField(null=True, blank=True)
    actor_name = models.CharField(max_length=150, blank=True, default="")
    action = models.CharField(max_length=80)
    operation_id = models.UUIDField(null=True, blank=True)
    profile_key = models.SlugField(max_length=80, blank=True, default="")
    outcome = models.CharField(max_length=24, default="recorded")
    evidence = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["action", "created_at"])]
