from django.db import migrations, models
from django.db.models import Q


def keep_latest_active_generation(apps, schema_editor):
    ArtifactGeneration = apps.get_model("core", "ArtifactGeneration")
    active = list(
        ArtifactGeneration.objects.filter(status="active").order_by(
            "-promoted_at", "-created_at", "-pk"
        )
    )
    if not active:
        return
    winner = active[0]
    for generation in active[1:]:
        generation.status = "superseded"
        generation.superseded_by_id = winner.pk
        generation.save(update_fields=["status", "superseded_by"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0019_sitesetting"),
    ]

    operations = [
        migrations.RunPython(keep_latest_active_generation, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="artifactgeneration",
            constraint=models.UniqueConstraint(
                fields=("status",),
                condition=Q(status="active"),
                name="single_active_artifact_generation",
            ),
        ),
    ]
