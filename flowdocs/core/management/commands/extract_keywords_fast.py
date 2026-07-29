from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Retired legacy keyword extraction command"

    def add_arguments(self, parser):
        parser.add_argument("--max-chars", type=int, default=50000)

    def handle(self, *args, **options):
        raise CommandError(
            "extract_keywords_fast is retired because no supported extraction "
            "backend is configured. Manage document keywords through the "
            "supported document workflow."
        )
