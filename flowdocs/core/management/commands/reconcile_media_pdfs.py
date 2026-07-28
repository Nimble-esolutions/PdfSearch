from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import CustomUser, Folder, PDFFile
from core.utils import SearchDataIntegrityError, precompute_pdf_embeddings


class Command(BaseCommand):
    help = "Reconcile PDFs already present under the configured MEDIA_ROOT"

    def add_arguments(self, parser):
        parser.add_argument(
            "--folder-id",
            type=int,
            default=22,
            help="Folder ID to assign PDFs to (default: 22)",
        )

    def handle(self, *args, **options):
        media_root = Path(settings.MEDIA_ROOT).expanduser().resolve()
        media_pdfs_dir = media_root / "pdfs"
        folder_id = options["folder_id"]

        try:
            folder = Folder.objects.get(id=folder_id)
        except Folder.DoesNotExist as exc:
            raise CommandError(f"Folder with ID {folder_id} not found") from exc

        user = CustomUser.objects.filter(role="superadmin").first() or CustomUser.objects.first()
        if user is None:
            raise CommandError("No users found in database")

        if not media_pdfs_dir.is_dir():
            raise CommandError(f"Media PDFs directory not found: {media_pdfs_dir}")

        pdf_files = sorted(media_pdfs_dir.rglob("*.pdf"))
        self.stdout.write(
            f"Using folder: {folder.name} (ID: {folder_id}); "
            f"media root: {media_root}"
        )
        self.stdout.write(f"Found {len(pdf_files)} PDF files in media directory")

        restored_count = 0
        skipped_count = 0
        failures: list[str] = []

        for pdf_path in pdf_files:
            relative_path = pdf_path.relative_to(media_root).as_posix()
            if PDFFile.objects.filter(file=relative_path).exists():
                self.stdout.write(f"Skipping {relative_path} - already in database")
                skipped_count += 1
                continue

            try:
                with transaction.atomic():
                    with pdf_path.open("rb") as stream:
                        pdf_file = PDFFile.objects.create(
                            title=pdf_path.stem.replace("_", " ").title(),
                            uploaded_by=user,
                            folder=folder,
                            file=relative_path,
                        )
                    # The source file is already in MEDIA_ROOT. File() is opened
                    # above to verify it is readable without copying it elsewhere.
                    if not pdf_file.file.storage.exists(pdf_file.file.name):
                        raise SearchDataIntegrityError(
                            f"Restored file is not readable through configured storage: {relative_path}"
                        )
                    precompute_pdf_embeddings(pdf_file)

                self.stdout.write(
                    self.style.SUCCESS(f"Restored {relative_path} and built searchable artifacts")
                )
                restored_count += 1
            except Exception as exc:
                failures.append(f"{relative_path}: {exc}")
                self.stdout.write(self.style.ERROR(f"Failed to restore {relative_path}: {exc}"))

        if failures:
            raise CommandError(
                f"Restoration failed for {len(failures)} PDF(s); "
                f"{restored_count} restored, {skipped_count} skipped"
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Restoration complete: {restored_count} PDFs restored, {skipped_count} skipped"
            )
        )
