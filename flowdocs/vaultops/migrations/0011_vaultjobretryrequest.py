from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("vaultops", "0010_activationintent_request_idempotency"),
    ]

    operations = [
        migrations.CreateModel(
            name="VaultJobRetryRequest",
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
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("idempotency_key", models.CharField(max_length=160)),
                ("requested_state_version", models.PositiveBigIntegerField()),
                ("resulting_state_version", models.PositiveBigIntegerField()),
                ("resulting_retry_count", models.PositiveIntegerField()),
                (
                    "mode",
                    models.CharField(
                        choices=[
                            ("fresh_snapshot", "Fresh snapshot"),
                            ("checkpoint_resume", "Checkpoint resume"),
                            ("operation_retry", "Operation retry"),
                        ],
                        max_length=24,
                    ),
                ),
                ("actor_id", models.PositiveBigIntegerField(blank=True, null=True)),
                ("actor_name", models.CharField(blank=True, default="", max_length=150)),
                (
                    "job",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="retry_requests",
                        to="vaultops.vaultjob",
                    ),
                ),
            ],
            options={"ordering": ["created_at"]},
        ),
        migrations.AddConstraint(
            model_name="vaultjobretryrequest",
            constraint=models.UniqueConstraint(
                fields=("job", "idempotency_key"),
                name="vaultops_unique_job_retry_request",
            ),
        ),
        migrations.AddField(
            model_name="sourcesnapshot",
            name="cleanup_attempts",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="sourcesnapshot",
            name="cleanup_error_code",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
        migrations.AddField(
            model_name="sourcesnapshot",
            name="cleanup_not_before",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="sourcesnapshot",
            name="cleanup_path",
            field=models.CharField(blank=True, default="", max_length=1000),
        ),
        migrations.AddField(
            model_name="sourcesnapshot",
            name="cleanup_state",
            field=models.CharField(
                choices=[
                    ("none", "No cleanup"),
                    ("pending", "Cleanup pending"),
                    ("reclaiming", "Cleanup in progress"),
                    ("completed", "Cleanup completed"),
                    ("failed", "Cleanup failed"),
                ],
                default="none",
                max_length=16,
            ),
        ),
    ]
