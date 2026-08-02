from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models
import uuid
from django.conf import settings

# ---------------- Custom User ----------------
class CustomUserManager(UserManager):
    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields["role"] = "superadmin"
        return super().create_superuser(username, email, password, **extra_fields)


class CustomUser(AbstractUser):
    ROLE_CHOICES = (
        ('superadmin', 'SuperAdmin'),
        ('admin', 'Admin'),
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    department = models.CharField(max_length=100, blank=True, null=True)
    objects = CustomUserManager()

# ---------------- Folder ----------------
from django.db import models

class Folder(models.Model):
    name = models.CharField(max_length=200, unique=True)
    created_by = models.ForeignKey(CustomUser, null=False, blank=False, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    
    # --- New field for folder keywords ---
    keywords = models.JSONField(default=list, blank=True)  # stores keywords as a list of strings

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


SEARCHABLE_PDF_LIFECYCLES = ("uploaded", "processing", "ready")


# ---------------- PDF File ----------------
class PDFFile(models.Model):
    PROCESSING_STATUS_CHOICES = (
        ("queued", "Queued"),
        ("running", "Running"),
        ("ready", "Ready"),
        ("failed", "Failed"),
    )

    title = models.CharField(max_length=200)
    file = models.FileField(upload_to="pdfs/")
    # in your models.py (PDFFile)
    extracted_text = models.TextField(null=True, blank=True)         # Newly added
    page_chunks = models.JSONField(default=list, blank=True)         # list[str] Newly added
    chunk_embeddings = models.JSONField(default=list, blank=True)    # list[list[float]] Newly added

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="uploaded_pdfs",
        blank=True,
        null=True,
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    folder = models.ForeignKey(
        "Folder", on_delete=models.CASCADE, related_name="files", null=True, blank=True
    )
    
    # --- New fields for search optimization ---
    keywords = models.JSONField(default=list, blank=True)  # store keywords safely
    text_content = models.TextField(blank=True, default="")  # store extracted PDF text

    category = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        help_text="acts / rules / bylaws",
    )
    file_path = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Relative path like housing/acts/154B.pdf",
    )
    indexed = models.BooleanField(default=False, help_text="FAISS index built or not")
    processing_status = models.CharField(
        max_length=16,
        choices=PROCESSING_STATUS_CHOICES,
        default="ready",
        help_text="Durable OCR, embedding, and indexing status",
    )
    processing_attempts = models.PositiveIntegerField(default=0)
    processing_error_code = models.CharField(max_length=64, blank=True, default="")
    processing_error_message = models.TextField(blank=True, default="")
    processing_started_at = models.DateTimeField(null=True, blank=True)
    processing_finished_at = models.DateTimeField(null=True, blank=True)
    ocr_metadata = models.JSONField(default=dict, blank=True)
    embedding_provider = models.CharField(max_length=32, blank=True, default="")
    embedding_model = models.CharField(max_length=128, blank=True, default="")
    embedding_dimension = models.PositiveIntegerField(null=True, blank=True)
    lifecycle = models.CharField(
        max_length=20,
        choices=(
            ("uploaded", "Uploaded"),
            ("processing", "Processing"),
            ("ready", "Ready"),
            ("deprecated", "Deprecated"),
            ("archived", "Archived"),
            ("unavailable", "Unavailable"),
        ),
        default="uploaded",
        help_text="Document lifecycle state for search and visibility control",
    )
    media_prior_lifecycle = models.CharField(max_length=20, blank=True, default="")
    media_expected_sha256 = models.CharField(max_length=64, blank=True, default="")
    media_expected_size = models.PositiveBigIntegerField(null=True, blank=True)
    media_quarantine_reason = models.CharField(max_length=80, blank=True, default="")
    media_case_reference = models.CharField(max_length=80, blank=True, default="")
    media_observed_at = models.DateTimeField(null=True, blank=True)
    subject = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        help_text="housing / audit / agriculture",
    )

    def delete(self, *args, **kwargs):
        """
        Ensure the file is deleted from storage when the database entry is removed.
        """
        if self.file:
            self.file.delete(save=False)
        super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.title} (Folder: {self.folder.name if self.folder else 'No Folder'})"


class ArtifactGeneration(models.Model):
    """An immutable, validated snapshot of application data and derived indexes."""

    STATUS_CHOICES = (
        ("staged", "Staged"),
        ("validated", "Validated"),
        ("active", "Active"),
        ("failed", "Failed"),
        ("superseded", "Superseded"),
        ("purged", "Purged"),
    )
    RETENTION_CHOICES = (
        ("keep_all", "Keep all"),
        ("keep_last_n", "Keep last N"),
        ("age_based", "Age based"),
    )

    generation_id = models.CharField(max_length=120, unique=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="staged")
    manifest = models.JSONField(default=dict, blank=True)
    source = models.CharField(max_length=40, default="local")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="artifact_generations",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    validated_at = models.DateTimeField(null=True, blank=True)
    promoted_at = models.DateTimeField(null=True, blank=True)
    superseded_by = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="predecessors",
    )
    retention_policy = models.CharField(
        max_length=20, choices=RETENTION_CHOICES, default="keep_all"
    )
    retention_value = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="N for keep_last_n, or days for age_based",
    )
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class MaintenanceJob(models.Model):
    """Durable queue entry for indexing, validation, sync, and restore work."""

    KIND_CHOICES = (
        ("validate", "Validate data"),
        ("reindex_needed", "Reindex needed"),
        ("reindex_all", "Reindex all"),
        ("reindex_selected", "Reindex selected"),
        ("process_pdf", "Process uploaded PDF"),
        ("repair_indexes", "Repair stored indexes"),
        ("sync_generation", "Sync generation"),
        ("restore_generation", "Restore generation"),
        ("promote_generation", "Promote generation"),
        ("rollback_generation", "Rollback generation"),
        ("purge_generation", "Purge generation"),
    )
    STATUS_CHOICES = (
        ("queued", "Queued"),
        ("running", "Running"),
        ("paused", "Paused"),
        ("cancel_requested", "Cancel requested"),
        ("completed", "Completed"),
        ("failed", "Failed"),
        ("cancelled", "Cancelled"),
    )

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    kind = models.CharField(max_length=32, choices=KIND_CHOICES)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default="queued")
    scope = models.JSONField(default=dict, blank=True)
    options = models.JSONField(default=dict, blank=True)
    total_items = models.PositiveIntegerField(default=0)
    completed_items = models.PositiveIntegerField(default=0)
    failed_items = models.PositiveIntegerField(default=0)
    error_summary = models.TextField(blank=True, default="")
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="maintenance_jobs",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["kind", "created_at"]),
        ]


class MaintenancePlan(models.Model):
    """Short-lived, server-calculated authority for a local maintenance job."""

    OPERATION_CHOICES = (
        ("validate", "Validate files"),
        ("repair_indexes", "Repair stored indexes"),
        ("reindex_needed", "Reindex needed"),
        ("reindex_selected", "Reindex selected"),
    )
    STATE_CHOICES = (
        ("previewed", "Previewed"),
        ("queued", "Queued"),
        ("expired", "Expired"),
        ("rejected", "Rejected"),
    )

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    operation = models.CharField(max_length=32, choices=OPERATION_CHOICES)
    state = models.CharField(
        max_length=16, choices=STATE_CHOICES, default="previewed"
    )
    selection = models.JSONField(default=dict)
    preview = models.JSONField(default=dict)
    source_digest = models.CharField(max_length=64)
    state_version = models.CharField(max_length=64)
    idempotency_key = models.CharField(max_length=128)
    external_embeddings_required = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="maintenance_plans",
    )
    job = models.OneToOneField(
        MaintenanceJob,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="maintenance_plan",
    )
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["operation", "idempotency_key"],
                name="core_maintenanceplan_operation_idempotency",
            ),
        ]
        indexes = [
            models.Index(fields=["state", "expires_at"]),
            models.Index(fields=["created_by", "created_at"]),
        ]


class MaintenanceJobItem(models.Model):
    """Per-document result for resumable maintenance jobs."""

    STATUS_CHOICES = (
        ("queued", "Queued"),
        ("running", "Running"),
        ("completed", "Completed"),
        ("failed", "Failed"),
        ("skipped", "Skipped"),
    )

    job = models.ForeignKey(MaintenanceJob, on_delete=models.CASCADE, related_name="items")
    pdf = models.ForeignKey(
        PDFFile,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="maintenance_items",
    )
    folder = models.ForeignKey(
        Folder,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="maintenance_items",
    )
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="queued")
    attempts = models.PositiveIntegerField(default=0)
    error_code = models.CharField(max_length=64, blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["job", "pdf"], name="unique_maintenance_pdf_per_job"),
        ]
        indexes = [
            models.Index(fields=["job", "status"]),
        ]


class ArtifactValidation(models.Model):
    """Structured validation record for an artifact generation."""

    VALIDATION_TYPE_CHOICES = (
        ("manifest", "Manifest"),
        ("sha256", "SHA-256"),
        ("counts", "Counts"),
        ("faiss", "FAISS"),
        ("search", "Search"),
    )
    STATUS_CHOICES = (
        ("passed", "Passed"),
        ("failed", "Failed"),
        ("warned", "Warned"),
    )

    generation = models.ForeignKey(
        ArtifactGeneration,
        on_delete=models.CASCADE,
        related_name="validations",
    )
    validation_type = models.CharField(max_length=20, choices=VALIDATION_TYPE_CHOICES)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES)
    details = models.JSONField(default=dict, blank=True)
    validated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="artifact_validations",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class MaintenanceAuditEvent(models.Model):
    """Append-only audit trail for maintenance job state transitions."""

    EVENT_TYPE_CHOICES = (
        ("queued", "Queued"),
        ("claimed", "Claimed"),
        ("item_completed", "Item completed"),
        ("item_failed", "Item failed"),
        ("cancelled", "Cancelled"),
        ("retried", "Retried"),
        ("completed", "Completed"),
        ("failed", "Failed"),
        ("promoted", "Promoted"),
        ("rolled_back", "Rolled back"),
        ("purged", "Purged"),
        ("worker_died", "Worker died"),
        ("media_unavailable", "Media unavailable"),
        ("media_evidence_bound", "Media recovery evidence bound"),
        ("media_restored", "Media restored"),
    )

    job = models.ForeignKey(
        MaintenanceJob,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_events",
    )
    event_type = models.CharField(max_length=24, choices=EVENT_TYPE_CHOICES)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="maintenance_audit_events",
    )
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class SiteSetting(models.Model):
    """Runtime-overridable site settings persisted in the database.

    Settings are read in order: database → cache → environment variable.
    A value stored here takes precedence over the corresponding
    environment variable.  This allows superadmins to toggle feature
    flags without restarting the container.
    """

    key = models.CharField(max_length=128, unique=True)
    value = models.TextField(blank=True, default="")
    description = models.TextField(blank=True, default="")
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Site Setting"
        verbose_name_plural = "Site Settings"
        ordering = ["key"]

    def __str__(self):
        return f"{self.key} = {self.value[:60]}" if self.value else f"{self.key} (empty)"
