from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("dataops", "0003_operation_lease")]

    operations = [
        migrations.AddField(
            model_name="dataprofile",
            name="provider",
            field=models.CharField(
                choices=[
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
                ],
                default="generic",
                max_length=32,
            ),
        ),
        migrations.AddField(model_name="dataprofile", name="addressing_style", field=models.CharField(choices=[("auto", "Automatic"), ("virtual", "Virtual hosted"), ("path", "Path style")], default="auto", max_length=12)),
        migrations.AddField(model_name="dataprofile", name="signature_version", field=models.CharField(default="s3v4", max_length=16)),
        migrations.AddField(model_name="dataprofile", name="verify_tls", field=models.BooleanField(default=True)),
        migrations.AddField(model_name="dataprofile", name="custom_ca_reference", field=models.CharField(blank=True, default="", max_length=255)),
        migrations.AddField(model_name="datacredential", name="access_key_nonce", field=models.CharField(blank=True, default="", max_length=64)),
        migrations.AddField(model_name="datacredential", name="secret_nonce", field=models.CharField(blank=True, default="", max_length=64)),
        migrations.CreateModel(
            name="DataOpsSetting",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("key", models.CharField(max_length=120, unique=True)),
                ("value", models.CharField(blank=True, default="", max_length=500)),
            ],
        ),
        migrations.CreateModel(
            name="BackupJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=160)),
                ("slug", models.SlugField(max_length=100, unique=True)),
                ("source_profile_key", models.SlugField(max_length=80)),
                ("target_profile_key", models.SlugField(max_length=80)),
                ("source_prefix", models.CharField(blank=True, default="", max_length=500)),
                ("target_prefix", models.CharField(blank=True, default="", max_length=500)),
                ("mode", models.CharField(choices=[("incremental", "Incremental"), ("archive", "Archive / versioned"), ("mirror", "Mirror")], default="incremental", max_length=16)),
                ("schedule", models.CharField(blank=True, default="", max_length=120)),
                ("timezone", models.CharField(default="UTC", max_length=80)),
                ("enabled", models.BooleanField(default=True)),
                ("delete_orphans", models.BooleanField(default=False)),
                ("max_parallel_transfers", models.PositiveSmallIntegerField(default=4)),
                ("bandwidth_limit_bps", models.PositiveBigIntegerField(default=0)),
                ("multipart_threshold_bytes", models.PositiveBigIntegerField(default=67108864)),
                ("multipart_chunk_size_bytes", models.PositiveBigIntegerField(default=16777216)),
                ("retry_limit", models.PositiveSmallIntegerField(default=5)),
                ("last_run_at", models.DateTimeField(blank=True, null=True)),
                ("last_run_status", models.CharField(blank=True, default="", max_length=24)),
            ],
            options={"ordering": ["name"]},
        ),
    ]
