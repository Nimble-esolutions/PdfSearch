from django.contrib.auth.models import AbstractUser
from django.db import models
import uuid
from django.conf import settings

# ---------------- Custom User ----------------
class CustomUser(AbstractUser):
    ROLE_CHOICES = (
        ('superadmin', 'SuperAdmin'),
        ('admin', 'Admin'),
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    department = models.CharField(max_length=100, blank=True, null=True)

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


# ---------------- PDF File ----------------
class PDFFile(models.Model):
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

    class Meta:
        ordering = ["-created_at"]


class MaintenanceJob(models.Model):
    """Durable queue entry for indexing, validation, sync, and restore work."""

    KIND_CHOICES = (
        ("validate", "Validate data"),
        ("reindex_needed", "Reindex needed"),
        ("reindex_all", "Reindex all"),
        ("repair_indexes", "Repair stored indexes"),
        ("sync_generation", "Sync generation"),
        ("restore_generation", "Restore generation"),
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
