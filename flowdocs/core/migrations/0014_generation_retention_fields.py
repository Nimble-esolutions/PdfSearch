from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0013_artifactgeneration_maintenancejob"),
    ]

    operations = [
        migrations.AddField(
            model_name="artifactgeneration",
            name="superseded_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="predecessors",
                to="core.artifactgeneration",
            ),
        ),
        migrations.AddField(
            model_name="artifactgeneration",
            name="retention_policy",
            field=models.CharField(
                choices=[
                    ("keep_all", "Keep all"),
                    ("keep_last_n", "Keep last N"),
                    ("age_based", "Age based"),
                ],
                default="keep_all",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="artifactgeneration",
            name="retention_value",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="N for keep_last_n, or days for age_based",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="artifactgeneration",
            name="expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="artifactgeneration",
            name="status",
            field=models.CharField(
                choices=[
                    ("staged", "Staged"),
                    ("validated", "Validated"),
                    ("active", "Active"),
                    ("failed", "Failed"),
                    ("superseded", "Superseded"),
                    ("purged", "Purged"),
                ],
                default="staged",
                max_length=20,
            ),
        ),
    ]