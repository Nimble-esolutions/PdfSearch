from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0019_sitesetting"),
    ]

    operations = [
        migrations.AddField(
            model_name="pdffile",
            name="is_public",
            field=models.BooleanField(
                default=False,
                help_text="Explicitly allow this document in anonymous public search and viewing",
            ),
        ),
    ]
