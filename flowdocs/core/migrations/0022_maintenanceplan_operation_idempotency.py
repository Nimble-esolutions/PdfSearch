from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0021_maintenanceplan"),
    ]

    operations = [
        migrations.AlterField(
            model_name="maintenanceplan",
            name="idempotency_key",
            field=models.CharField(max_length=128),
        ),
        migrations.AddConstraint(
            model_name="maintenanceplan",
            constraint=models.UniqueConstraint(
                fields=["operation", "idempotency_key"],
                name="core_maintenanceplan_operation_idempotency",
            ),
        ),
    ]
