from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0023_unavailable_media_lifecycle"),
    ]

    operations = [
        migrations.AddField(
            model_name="pdffile",
            name="media_case_reference",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
        migrations.AddField(
            model_name="pdffile",
            name="media_expected_sha256",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="pdffile",
            name="media_expected_size",
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="pdffile",
            name="media_observed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="pdffile",
            name="media_prior_lifecycle",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        migrations.AddField(
            model_name="pdffile",
            name="media_quarantine_reason",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
    ]
