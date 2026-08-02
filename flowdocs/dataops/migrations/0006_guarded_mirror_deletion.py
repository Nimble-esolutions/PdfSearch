from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    dependencies = [("dataops", "0005_dataoperation_sync_kind")]

    operations = [
        migrations.AddField(model_name="backupjob", name="mirror_delete_max_objects", field=models.PositiveIntegerField(default=100)),
        migrations.AddField(model_name="backupjob", name="mirror_delete_max_percent", field=models.PositiveSmallIntegerField(default=10)),
        migrations.CreateModel(
            name="MirrorDeletionPreview",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("state", models.CharField(choices=[("ready", "Ready for confirmation"), ("applied", "Applied"), ("expired", "Expired"), ("rejected", "Rejected")], default="ready", max_length=16)),
                ("digest", models.CharField(max_length=64)),
                ("object_keys", models.JSONField(default=list)),
                ("target_object_count", models.PositiveIntegerField(default=0)),
                ("total_bytes", models.PositiveBigIntegerField(default=0)),
                ("expires_at", models.DateTimeField()),
                ("confirmed_operation", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="mirror_deletion_preview", to="dataops.dataoperation")),
                ("job", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="deletion_previews", to="dataops.backupjob")),
            ],
        ),
    ]
