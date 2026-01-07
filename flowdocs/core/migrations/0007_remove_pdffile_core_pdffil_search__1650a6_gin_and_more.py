from django.db import migrations

class Migration(migrations.Migration):

    dependencies = [
        ('core', '0006_pdffile_content_pdffile_search_vector_and_more'),
    ]

    operations = [
        migrations.RunSQL(
            sql="DROP INDEX IF EXISTS core_pdffil_search__1650a6_gin;",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
