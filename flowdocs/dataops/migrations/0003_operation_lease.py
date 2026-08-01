from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("dataops", "0002_profile_manifest_pipeline")]

    operations = [
        migrations.AddField(
            model_name="dataoperation",
            name="lease_token",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="dataoperation",
            name="lease_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="dataoperation",
            index=models.Index(fields=["state", "lease_expires_at"], name="dataops_op_state_lease_idx"),
        ),
    ]
