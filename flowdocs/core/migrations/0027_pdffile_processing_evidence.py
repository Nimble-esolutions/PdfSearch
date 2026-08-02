from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0026_maintenancejob_process_pdf"),
    ]

    operations = [
        migrations.AddField(
            model_name="pdffile",
            name="embedding_dimension",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="pdffile",
            name="embedding_model",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="pdffile",
            name="embedding_provider",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="pdffile",
            name="ocr_metadata",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="pdffile",
            name="processing_attempts",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="pdffile",
            name="processing_error_code",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="pdffile",
            name="processing_error_message",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="pdffile",
            name="processing_finished_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="pdffile",
            name="processing_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="pdffile",
            name="processing_status",
            field=models.CharField(
                choices=[
                    ("queued", "Queued"),
                    ("running", "Running"),
                    ("ready", "Ready"),
                    ("failed", "Failed"),
                ],
                default="ready",
                help_text="Durable OCR, embedding, and indexing status",
                max_length=16,
            ),
        ),
    ]
