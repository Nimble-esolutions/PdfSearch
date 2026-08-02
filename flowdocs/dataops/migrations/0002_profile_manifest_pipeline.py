from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("dataops", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="dataprofile",
            name="namespace",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
        migrations.AddField(
            model_name="dataprofile",
            name="prefix",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
        migrations.AddField(
            model_name="dataprofile",
            name="credential_ref",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AlterField(
            model_name="dataprofile",
            name="source",
            field=models.CharField(
                choices=[
                    ("environment", "Environment"),
                    ("stored", "Stored fallback"),
                    ("default", "Safe default"),
                ],
                default="stored",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="dataoperation",
            name="destination_profile_key",
            field=models.SlugField(blank=True, default="", max_length=80),
        ),
        migrations.AddField(
            model_name="dataoperation",
            name="pipeline_stage",
            field=models.CharField(blank=True, default="preflight", max_length=32),
        ),
        migrations.AddField(
            model_name="dataoperation",
            name="release_id",
            field=models.CharField(blank=True, default="", max_length=160),
        ),
        migrations.AddField(
            model_name="dataoperation",
            name="source_profile_key",
            field=models.SlugField(blank=True, default="", max_length=80),
        ),
    ]
