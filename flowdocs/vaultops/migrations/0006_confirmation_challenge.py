import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("vaultops", "0005_activation_supervisor_evidence"),
    ]

    operations = [
        migrations.CreateModel(
            name="ConfirmationChallenge",
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
                (
                    "public_id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        unique=True,
                    ),
                ),
                ("actor_id", models.PositiveBigIntegerField()),
                ("action", models.CharField(max_length=80)),
                ("target", models.CharField(max_length=200)),
                ("state_digest", models.CharField(max_length=64)),
                ("phrase_salt", models.CharField(max_length=64)),
                ("phrase_digest", models.CharField(max_length=64)),
                ("expires_at", models.DateTimeField()),
                ("used_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="confirmationchallenge",
            index=models.Index(
                fields=["actor_id", "action", "expires_at"],
                name="vaultops_co_actor_i_c24ad8_idx",
            ),
        ),
    ]
