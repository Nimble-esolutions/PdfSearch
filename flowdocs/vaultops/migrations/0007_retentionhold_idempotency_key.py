from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("vaultops", "0006_confirmation_challenge"),
    ]

    operations = [
        migrations.AddField(
            model_name="retentionhold",
            name="idempotency_key",
            field=models.CharField(blank=True, default="", max_length=160),
        ),
        migrations.AddConstraint(
            model_name="retentionhold",
            constraint=models.UniqueConstraint(
                condition=~models.Q(idempotency_key=""),
                fields=("generation", "idempotency_key"),
                name="vaultops_hold_generation_idempotency_uniq",
            ),
        ),
    ]
