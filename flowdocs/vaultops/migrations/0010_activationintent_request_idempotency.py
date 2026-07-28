from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("vaultops", "0009_restoreworkspace_import_idempotency"),
    ]

    operations = [
        migrations.AddField(
            model_name="activationintent",
            name="idempotency_key",
            field=models.CharField(blank=True, default="", max_length=160),
        ),
        migrations.AddField(
            model_name="activationintent",
            name="request_state_digest",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddConstraint(
            model_name="activationintent",
            constraint=models.UniqueConstraint(
                condition=~models.Q(idempotency_key=""),
                fields=("deployment_id", "idempotency_key"),
                name="vaultops_unique_activation_request",
            ),
        ),
    ]
