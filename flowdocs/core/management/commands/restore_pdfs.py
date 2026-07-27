"""One-release compatibility alias for the renamed media reconciliation tool."""

from core.management.commands.reconcile_media_pdfs import (
    Command as ReconcileMediaPDFsCommand,
)
from core.management.commands.reconcile_media_pdfs import precompute_pdf_embeddings


class Command(ReconcileMediaPDFsCommand):
    help = (
        "DEPRECATED alias for reconcile_media_pdfs; this reconciles PDF media "
        "and search artifacts and is not database disaster recovery"
    )

    def handle(self, *args, **options):
        self.stderr.write(
            self.style.WARNING(
                "restore_pdfs is deprecated; use reconcile_media_pdfs. "
                "This command is not database disaster recovery."
            )
        )
        from core.management.commands import reconcile_media_pdfs

        reconcile_media_pdfs.precompute_pdf_embeddings = precompute_pdf_embeddings
        return super().handle(*args, **options)
