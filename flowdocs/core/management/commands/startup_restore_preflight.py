from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.environment import RestorePolicy


STARTUP_POLICIES = {
    RestorePolicy.STARTUP_LATEST,
    RestorePolicy.STARTUP_PINNED,
}
RESTORE_REQUIRED_CODE = "startup_restore_required_but_unavailable"


def evaluate_startup_restore_preflight(identity, database_path):
    """Refuse implicit empty-database creation under a startup restore policy."""
    if identity.restore_policy not in STARTUP_POLICIES:
        return "policy_allows_normal_startup"

    path = Path(database_path)
    if path.is_file() and path.stat().st_size > 0:
        return "existing_database_preserved"

    raise CommandError(RESTORE_REQUIRED_CODE)


class Command(BaseCommand):
    help = "Fail closed before startup restore policy can create an empty database"

    def handle(self, *args, **options):
        posture = evaluate_startup_restore_preflight(
            settings.ENV_IDENTITY,
            settings.DATABASES["default"]["NAME"],
        )
        self.stdout.write(
            f"startup_restore_preflight_ok posture={posture}"
        )
