from django.db import migrations, models, connection


def remove_gin_index(apps, schema_editor):
    """
    Safely remove PostgreSQL-only GIN index.
    SQLite will skip this entirely.
    """
    if connection.vendor == "postgresql":
        schema_editor.execute(
            "DROP INDEX IF EXISTS core_pdffil_search__1650a6_gin;"
        )


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0006_pdffile_content_pdffile_search_vector_and_more'),
    ]

    operations = [
        migrations.RunPython(remove_gin_index, migrations.RunPython.noop),

        migrations.RemoveField(
            model_name='pdffile',
            name='content',
        ),
        migrations.RemoveField(
            model_name='pdffile',
            name='search_vector',
        ),
        migrations.AddField(
            model_name='pdffile',
            name='text_content',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AlterField(
            model_name='pdffile',
            name='keywords',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
