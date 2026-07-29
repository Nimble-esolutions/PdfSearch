from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Retired legacy keyword extraction command"

    def handle(self, *args, **options):
        raise CommandError(
            "extract_keywords is retired because no supported extraction "
            "backend is configured. Manage document keywords through the "
            "supported document workflow."
        )
