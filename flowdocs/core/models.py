from django.contrib.auth.models import AbstractUser
from django.db import models
from django.conf import settings

# ==========================================================
# Custom User
# ==========================================================
class CustomUser(AbstractUser):
    ROLE_CHOICES = (
        ('superadmin', 'SuperAdmin'),
        ('admin', 'Admin'),
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    department = models.CharField(max_length=100, blank=True, null=True)

    def __str__(self):
        return self.username


# ==========================================================
# Folder (Logical grouping + dominance detection)
# ==========================================================
class Folder(models.Model):
    name = models.CharField(max_length=200, unique=True)
    created_by = models.ForeignKey(
        CustomUser, null=False, blank=False, on_delete=models.CASCADE
    )
    created_at = models.DateTimeField(auto_now_add=True)

    # Keyword list used for folder dominance scoring
    keywords = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


# ==========================================================
# PDF File (Core Document Model)
# ==========================================================
class PDFFile(models.Model):
    # ---------------- Basic metadata ----------------
    title = models.CharField(max_length=200)

    # Raw uploaded file (kept for backward compatibility)
    file = models.FileField(upload_to="pdfs/")

    # uploaded_by = models.ForeignKey(
    #     settings.AUTH_USER_MODEL,
    #     on_delete=models.CASCADE,
    #     related_name="uploaded_pdfs"
    # )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="uploaded_pdfs"
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    folder = models.ForeignKey(
        Folder,
        on_delete=models.CASCADE,
        related_name="files",
        null=True,
        blank=True
    )

    # ---------------- Text & NLP ----------------
    extracted_text = models.TextField(null=True, blank=True)
    text_content = models.TextField(blank=True, default="")

    page_chunks = models.JSONField(default=list, blank=True)
    chunk_embeddings = models.JSONField(default=list, blank=True)

    # ---------------- Search optimization ----------------
    keywords = models.JSONField(default=list, blank=True)

    # ---------------- NEW: Legal / Subject routing ----------------
    subject = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        help_text="housing / audit / agriculture"
    )

    category = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        help_text="acts / rules / bylaws"
    )

    file_path = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Relative path like housing/acts/154B.pdf"
    )

    indexed = models.BooleanField(
        default=False,
        help_text="FAISS index built or not"
    )

    # ---------------- Cleanup ----------------
    def delete(self, *args, **kwargs):
        """
        Ensure the file is deleted from storage when the database entry is removed.
        """
        if self.file:
            self.file.delete(save=False)
        super().delete(*args, **kwargs)

    def __str__(self):
        folder_name = self.folder.name if self.folder else "No Folder"
        return f"{self.title} ({folder_name})"
