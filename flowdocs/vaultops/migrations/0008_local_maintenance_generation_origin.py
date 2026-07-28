from django.db import migrations, models


def classify_existing_generation_origins(apps, schema_editor):
    generation_model = apps.get_model("vaultops", "ArtifactGeneration")
    generation_model.objects.filter(
        legacy_database_id__isnull=False
    ).update(origin="legacy_projection")


def reset_generation_origins(apps, schema_editor):
    generation_model = apps.get_model("vaultops", "ArtifactGeneration")
    generation_model.objects.update(origin="vault_generation")


class Migration(migrations.Migration):
    dependencies = [
        ("vaultops", "0007_retentionhold_idempotency_key"),
    ]

    operations = [
        migrations.AddField(
            model_name="artifactgeneration",
            name="origin",
            field=models.CharField(
                choices=[
                    ("vault_generation", "Vault generation"),
                    ("local_maintenance", "Local maintenance"),
                    ("legacy_projection", "Legacy projection"),
                ],
                default="vault_generation",
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="artifactgeneration",
            name="lineage_job_public_id",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="artifactgeneration",
            name="parent_generation_id",
            field=models.CharField(blank=True, default="", max_length=160),
        ),
        migrations.AddField(
            model_name="artifactgeneration",
            name="parent_manifest_digest",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.RunPython(
            classify_existing_generation_origins,
            reset_generation_origins,
        ),
        migrations.AddConstraint(
            model_name="artifactgeneration",
            constraint=models.CheckConstraint(
                condition=(
                    ~models.Q(origin="local_maintenance")
                    | (
                        models.Q(lineage_job_public_id__isnull=False)
                        & ~models.Q(parent_generation_id="")
                        & ~models.Q(parent_manifest_digest="")
                    )
                ),
                name="vaultops_local_origin_has_lineage",
            ),
        ),
        migrations.AddConstraint(
            model_name="artifactgeneration",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(origin="local_maintenance")
                    | (
                        models.Q(lineage_job_public_id__isnull=True)
                        & models.Q(parent_generation_id="")
                        & models.Q(parent_manifest_digest="")
                    )
                ),
                name="vaultops_nonlocal_origin_has_no_lineage",
            ),
        ),
        migrations.AddConstraint(
            model_name="artifactgeneration",
            constraint=models.CheckConstraint(
                condition=(
                    ~models.Q(origin="local_maintenance")
                    | models.Q(vault_state="unknown")
                ),
                name="vaultops_local_origin_not_vault_authority",
            ),
        ),
        migrations.AddConstraint(
            model_name="artifactgeneration",
            constraint=models.UniqueConstraint(
                condition=models.Q(origin="local_maintenance"),
                fields=("lineage_job_public_id",),
                name="vaultops_unique_local_maintenance_job",
            ),
        ),
    ]
