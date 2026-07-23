from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0013_artifactgeneration_maintenancejob"),
    ]

    operations = [
        migrations.CreateModel(
            name="ArtifactValidation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("validation_type", models.CharField(
                    choices=[("manifest", "Manifest"), ("sha256", "SHA-256"), ("counts", "Counts"), ("faiss", "FAISS"), ("search", "Search")],
                    max_length=20,
                )),
                ("status", models.CharField(
                    choices=[("passed", "Passed"), ("failed", "Failed"), ("warned", "Warned")],
                    max_length=16,
                )),
                ("details", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("generation", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="validations",
                    to="core.artifactgeneration",
                )),
                ("validated_by", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="artifact_validations",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="MaintenanceAuditEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_type", models.CharField(
                    choices=[
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
                    ],
                    max_length=24,
                )),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="maintenance_audit_events",
                    to=settings.AUTH_USER_MODEL,
                )),
                ("job", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="audit_events",
                    to="core.maintenancejob",
                )),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="artifactvalidation",
            index=models.Index(fields=["generation", "validation_type"], name="core_artval_gen_type_idx"),
        ),
        migrations.AddIndex(
            model_name="maintenanceauditevent",
            index=models.Index(fields=["job", "created_at"], name="core_auditev_job_created_idx"),
        ),
        migrations.AddIndex(
            model_name="maintenanceauditevent",
            index=models.Index(fields=["event_type", "created_at"], name="core_auditev_type_created_idx"),
        ),
    ]