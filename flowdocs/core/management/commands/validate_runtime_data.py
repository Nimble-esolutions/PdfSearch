import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.runtime_data_gate import RuntimeDataGateError, validate_seed_pdf_media


class Command(BaseCommand):
    help = "Fail closed when a declared seed has PDF rows without media files"

    def add_arguments(self, parser):
        parser.add_argument("--declared-seed", required=True, type=Path)
        parser.add_argument(
            "--media-root",
            type=Path,
            default=Path(settings.MEDIA_ROOT),
        )
        parser.add_argument(
            "--mode",
            choices=("strict", "empty", "bootstrap"),
            default=os.getenv("DATA_BOOTSTRAP_MODE", "strict"),
        )

    def handle(self, *args, **options):
        if options["mode"] in {"empty", "bootstrap"}:
            self.stdout.write(
                self.style.WARNING(
                    f"Runtime data gate bypassed in explicit {options['mode']} mode"
                )
            )
            return

        try:
            report = validate_seed_pdf_media(options["declared_seed"], options["media_root"])
        except RuntimeDataGateError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            self.style.SUCCESS(f"Runtime data gate passed: {report['pdf_rows']} PDF row(s)")
        )
