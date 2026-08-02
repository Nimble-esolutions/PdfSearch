from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("dataops", "0004_universal_profiles_jobs")]

    operations = [
        migrations.AlterField(
            model_name="dataoperation",
            name="kind",
            field=models.CharField(
                choices=[
                    ("health_refresh", "Health refresh"),
                    ("backup", "Backup"),
                    ("restore", "Restore"),
                    ("reindex", "Reindex"),
                    ("auto_heal", "Automatic recovery"),
                    ("sync", "S3 backup and sync job"),
                ],
                max_length=24,
            ),
        )
    ]
