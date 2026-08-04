import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0027_pdffile_processing_evidence"),
    ]

    operations = [
        migrations.AlterField(
            model_name="pdffile",
            name="lifecycle",
            field=models.CharField(
                choices=[
                    ("intake", "Intake"),
                    ("uploaded", "Uploaded"),
                    ("processing", "Processing"),
                    ("ready", "Ready"),
                    ("deprecated", "Deprecated"),
                    ("archived", "Archived"),
                    ("unavailable", "Unavailable"),
                ],
                default="uploaded",
                help_text="Document lifecycle state for search and visibility control",
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name="UploadBatch",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "public_id",
                    models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("draft", "Draft"),
                            ("finalized", "Finalized"),
                            ("discarded", "Discarded"),
                            ("expired", "Expired"),
                        ],
                        default="draft",
                        max_length=16,
                    ),
                ),
                ("expires_at", models.DateTimeField()),
                ("finalized_at", models.DateTimeField(blank=True, null=True)),
                (
                    "manifest_sha256",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "folder",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="upload_batches",
                        to="core.folder",
                    ),
                ),
                (
                    "maintenance_job",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="upload_batches",
                        to="core.maintenancejob",
                    ),
                ),
                (
                    "uploader",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="upload_batches",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(
                        fields=["status", "expires_at"],
                        name="core_upload_status_9e8a9c_idx",
                    ),
                    models.Index(
                        fields=["uploader", "created_at"],
                        name="core_upload_uploade_7c9b75_idx",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="UploadBatchItem",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("idempotency_key", models.CharField(max_length=128)),
                ("original_filename", models.CharField(max_length=255)),
                ("title", models.CharField(max_length=200)),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("received", "Received"),
                            ("rejected", "Rejected"),
                            ("removed", "Removed"),
                        ],
                        max_length=16,
                    ),
                ),
                (
                    "checksum_sha256",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                (
                    "error_code",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                (
                    "error_message",
                    models.CharField(blank=True, default="", max_length=500),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "batch",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="items",
                        to="core.uploadbatch",
                    ),
                ),
                (
                    "pdf_file",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="upload_batch_items",
                        to="core.pdffile",
                    ),
                ),
            ],
            options={
                "ordering": ["created_at", "pk"],
                "indexes": [
                    models.Index(
                        fields=["batch", "state"],
                        name="core_upload_batch_i_48ab08_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("batch", "idempotency_key"),
                        name="uniq_upload_item_idempotency",
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(("pdf_file__isnull", False)),
                        fields=("pdf_file",),
                        name="uniq_upload_item_pdf",
                    ),
                ],
            },
        ),
    ]
