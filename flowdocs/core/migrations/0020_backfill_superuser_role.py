from django.db import migrations
import core.models


def backfill_superuser_role(apps, schema_editor):
    CustomUser = apps.get_model("core", "CustomUser")
    CustomUser.objects.filter(is_superuser=True, role="").update(
        role="superadmin"
    )


class Migration(migrations.Migration):
    dependencies = [("core", "0019_sitesetting")]
    operations = [
        migrations.AlterModelManagers(
            name="customuser",
            managers=[("objects", core.models.CustomUserManager())],
        ),
        migrations.RunPython(
            backfill_superuser_role, migrations.RunPython.noop
        ),
    ]
