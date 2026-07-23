from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0013_artifactgeneration_maintenancejob"),
    ]

    operations = [
        migrations.AddField(
            model_name="pdffile",
            name="lifecycle",
            field=models.CharField(
                choices=[
                    ("uploaded", "Uploaded"),
                    ("processing", "Processing"),
                    ("ready", "Ready"),
                    ("deprecated", "Deprecated"),
                    ("archived", "Archived"),
                ],
                default="uploaded",
                help_text="Document lifecycle state for search and visibility control",
                max_length=20,
            ),
        ),
    ]