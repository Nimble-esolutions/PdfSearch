from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("vaultops", "0008_local_maintenance_generation_origin"),
    ]

    operations = [
        migrations.AddField(
            model_name="restoreworkspace",
            name="import_idempotency_key",
            field=models.CharField(blank=True, default="", max_length=160),
        ),
        migrations.AddConstraint(
            model_name="restoreworkspace",
            constraint=models.UniqueConstraint(
                condition=~models.Q(import_idempotency_key=""),
                fields=("import_idempotency_key",),
                name="vaultops_unique_workspace_import_idempotency",
            ),
        ),
    ]
