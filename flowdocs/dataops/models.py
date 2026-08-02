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
    class Provider(models.TextChoices):
        AWS = "aws", "AWS S3"
        CLOUDFLARE_R2 = "cloudflare_r2", "Cloudflare R2"
        BACKBLAZE_B2 = "backblaze_b2", "Backblaze B2"
        WASABI = "wasabi", "Wasabi"
        DIGITALOCEAN = "digitalocean", "DigitalOcean Spaces"
        GCS = "gcs", "Google Cloud Storage interoperability"
        RUSTFS = "rustfs", "RustFS"
        MINIO = "minio", "MinIO"
        CEPH = "ceph", "Ceph Object Gateway"
        GARAGE = "garage", "Garage"
        SEAWEEDFS = "seaweedfs", "SeaweedFS"
        GENERIC = "generic", "Generic S3-compatible"

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
    provider = models.CharField(max_length=32, choices=Provider.choices, default=Provider.GENERIC)
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
    addressing_style = models.CharField(
        max_length=12,
        choices=(("auto", "Automatic"), ("virtual", "Virtual hosted"), ("path", "Path style")),
        default="auto",
    )
    signature_version = models.CharField(max_length=16, default="s3v4")
    verify_tls = models.BooleanField(default=True)
    custom_ca_reference = models.CharField(max_length=255, blank=True, default="")
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
    access_key_nonce = models.CharField(max_length=64, blank=True, default="")
    secret_nonce = models.CharField(max_length=64, blank=True, default="")
    key_version = models.PositiveSmallIntegerField(default=1)
    enabled = models.BooleanField(default=False)
    last_rotated_at = models.DateTimeField(null=True, blank=True)


class DataOpsSetting(TimeStampedModel):
    """Non-secret database fallbacks; deployed ENV remains authoritative."""

    key = models.CharField(max_length=120, unique=True)
    value = models.CharField(max_length=500, blank=True, default="")


class BackupJob(TimeStampedModel):
    class Mode(models.TextChoices):
        INCREMENTAL = "incremental", "Incremental"
        ARCHIVE = "archive", "Archive / versioned"
        MIRROR = "mirror", "Mirror"

    name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=100, unique=True)
    source_profile_key = models.SlugField(max_length=80)
    target_profile_key = models.SlugField(max_length=80)
    source_prefix = models.CharField(max_length=500, blank=True, default="")
    target_prefix = models.CharField(max_length=500, blank=True, default="")
    mode = models.CharField(max_length=16, choices=Mode.choices, default=Mode.INCREMENTAL)
    schedule = models.CharField(max_length=120, blank=True, default="")
    timezone = models.CharField(max_length=80, default="UTC")
    enabled = models.BooleanField(default=True)
    delete_orphans = models.BooleanField(default=False)
    max_parallel_transfers = models.PositiveSmallIntegerField(default=4)
    bandwidth_limit_bps = models.PositiveBigIntegerField(default=0)
    multipart_threshold_bytes = models.PositiveBigIntegerField(default=67108864)
    multipart_chunk_size_bytes = models.PositiveBigIntegerField(default=16777216)
    retry_limit = models.PositiveSmallIntegerField(default=5)
    mirror_delete_max_objects = models.PositiveIntegerField(default=100)
    mirror_delete_max_percent = models.PositiveSmallIntegerField(default=10)
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_run_status = models.CharField(max_length=24, blank=True, default="")

    class Meta:
        ordering = ["name"]


class MirrorDeletionPreview(TimeStampedModel):
    class State(models.TextChoices):
        READY = "ready", "Ready for confirmation"
        APPLIED = "applied", "Applied"
        EXPIRED = "expired", "Expired"
        REJECTED = "rejected", "Rejected"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    job = models.ForeignKey(BackupJob, on_delete=models.CASCADE, related_name="deletion_previews")
    state = models.CharField(max_length=16, choices=State.choices, default=State.READY)
    digest = models.CharField(max_length=64)
    object_keys = models.JSONField(default=list)
    target_object_count = models.PositiveIntegerField(default=0)
    total_bytes = models.PositiveBigIntegerField(default=0)
    expires_at = models.DateTimeField()
    confirmed_operation = models.OneToOneField(
        "DataOperation", null=True, blank=True, on_delete=models.PROTECT, related_name="mirror_deletion_preview"
    )


class DataOperation(TimeStampedModel):
    class Kind(models.TextChoices):
        HEALTH_REFRESH = "health_refresh", "Health refresh"
        BACKUP = "backup", "Backup"
        RESTORE = "restore", "Restore"
        REINDEX = "reindex", "Reindex"
        AUTO_HEAL = "auto_heal", "Automatic recovery"
        SYNC = "sync", "S3 backup and sync job"

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
