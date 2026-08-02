import uuid

import django.db.models.deletion
from django.db import migrations, models


PROVIDER_CHOICES = [
    ("aws", "AWS S3"),
    ("cloudflare_r2", "Cloudflare R2"),
    ("backblaze_b2", "Backblaze B2"),
    ("wasabi", "Wasabi"),
    ("digitalocean", "DigitalOcean Spaces"),
    ("gcs", "Google Cloud Storage interoperability"),
    ("rustfs", "RustFS"),
    ("minio", "MinIO"),
    ("ceph", "Ceph Object Gateway"),
    ("garage", "Garage"),
    ("seaweedfs", "SeaweedFS"),
    ("generic", "Generic S3-compatible"),
]


class Migration(migrations.Migration):
    dependencies = [("dataops", "0007_clone_rebind_kind")]

    operations = [
        migrations.CreateModel(
            name="DataConnection",
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
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "public_id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        unique=True,
                    ),
                ),
                ("name", models.CharField(max_length=160)),
                (
                    "provider",
                    models.CharField(
                        choices=PROVIDER_CHOICES,
                        default="generic",
                        max_length=32,
                    ),
                ),
                ("endpoint", models.CharField(max_length=500)),
                ("bucket", models.CharField(max_length=255)),
                ("region", models.CharField(blank=True, default="", max_length=80)),
                ("prefix", models.CharField(blank=True, default="v3", max_length=200)),
                ("dataset_id", models.CharField(max_length=120)),
                ("credential_ref", models.CharField(max_length=200)),
                ("enabled", models.BooleanField(default=True)),
                ("is_primary", models.BooleanField(default=False)),
                ("capabilities", models.JSONField(blank=True, default=dict)),
                ("observation", models.JSONField(blank=True, default=dict)),
                ("fingerprint", models.CharField(blank=True, default="", max_length=64)),
                ("last_probed_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "ordering": ["name"],
                "indexes": [
                    models.Index(
                        fields=["dataset_id", "enabled"],
                        name="dataops_dat_dataset_d5a93b_idx",
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("is_primary", True)),
                        fields=("dataset_id",),
                        name="dataops_one_primary_connection",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="DataPolicy",
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
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deployment_id", models.CharField(max_length=120, unique=True)),
                ("schema_version", models.PositiveSmallIntegerField(default=3)),
                (
                    "preset",
                    models.CharField(
                        choices=[
                            ("development", "Development"),
                            ("staging", "Staging"),
                            ("production", "Production"),
                            ("test", "Test"),
                        ],
                        default="development",
                        max_length=16,
                    ),
                ),
                ("values", models.JSONField(blank=True, default=dict)),
                ("fingerprint", models.CharField(blank=True, default="", max_length=64)),
            ],
        ),
        migrations.AddField(
            model_name="dataoperation",
            name="connection",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="operations",
                to="dataops.dataconnection",
            ),
        ),
        migrations.AddField(
            model_name="dataoperation",
            name="lifecycle_plan",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="dataoperation",
            name="lifecycle_plan_digest",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="dataoperation",
            name="lifecycle_route",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
        migrations.AlterField(
            model_name="dataoperation",
            name="kind",
            field=models.CharField(
                choices=[
                    ("health_refresh", "Health refresh"),
                    ("backup", "Backup"),
                    ("restore", "Restore"),
                    ("clone_rebind", "Clone / rebind"),
                    ("import", "Import"),
                    ("test_recovery", "Test recovery"),
                    ("rollback", "Rollback"),
                    ("reindex", "Reindex"),
                    ("auto_heal", "Automatic recovery"),
                    ("sync", "S3 backup and sync job"),
                ],
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="recoverypoint",
            name="activation_ready",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="recoverypoint",
            name="connection",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="recovery_points",
                to="dataops.dataconnection",
            ),
        ),
        migrations.AddField(
            model_name="recoverypoint",
            name="data_complete",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="recoverypoint",
            name="public_id",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
        migrations.AddField(
            model_name="recoverypoint",
            name="signature_key_id",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AlterField(
            model_name="recoverypoint",
            name="profile_key",
            field=models.SlugField(blank=True, default="", max_length=80),
        ),
    ]
