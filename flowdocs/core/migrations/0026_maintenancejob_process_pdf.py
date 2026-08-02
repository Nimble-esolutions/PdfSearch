from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0025_alter_maintenanceauditevent_event_type"),
    ]

    operations = [
        migrations.AlterField(
            model_name="maintenancejob",
            name="kind",
            field=models.CharField(
                choices=[
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
                ],
                max_length=32,
            ),
        ),
    ]
