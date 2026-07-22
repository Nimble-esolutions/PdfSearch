from .restore_pdfs import Command as RestorePDFsCommand


class Command(RestorePDFsCommand):
    """Compatibility alias that uses the same fail-closed restore pipeline."""

    help = "Restore PDFs using the canonical searchable-artifact pipeline"

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            "--skip-keywords",
            action="store_true",
            help="Retained for compatibility; keyword extraction is not part of restore",
        )
