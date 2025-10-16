from django.core.management.base import BaseCommand
from django.core.files import File
from core.models import PDFFile, CustomUser, Folder
from core.utils import extract_text_from_pdf
import os
from pathlib import Path

class Command(BaseCommand):
    help = 'Restore PDF files from media directory to database (simple version)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--folder-id',
            type=int,
            default=22,  # Default to Audit folder
            help='Folder ID to assign PDFs to (default: 22 for Audit)'
        )
        parser.add_argument(
            '--skip-keywords',
            action='store_true',
            help='Skip keyword extraction to speed up process'
        )

    def handle(self, *args, **options):
        media_pdfs_dir = Path('media/pdfs')
        folder_id = options['folder_id']
        skip_keywords = options['skip_keywords']
        
        try:
            folder = Folder.objects.get(id=folder_id)
            self.stdout.write(f"Using folder: {folder.name} (ID: {folder_id})")
        except Folder.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"Folder with ID {folder_id} not found"))
            return

        # Get the first superadmin user
        try:
            user = CustomUser.objects.filter(role='superadmin').first()
            if not user:
                user = CustomUser.objects.first()
            self.stdout.write(f"Using user: {user.username}")
        except CustomUser.DoesNotExist:
            self.stdout.write(self.style.ERROR("No users found in database"))
            return

        if not media_pdfs_dir.exists():
            self.stdout.write(self.style.ERROR(f"Media PDFs directory not found: {media_pdfs_dir}"))
            return

        pdf_files = list(media_pdfs_dir.glob('*.pdf'))
        self.stdout.write(f"Found {len(pdf_files)} PDF files in media directory")

        restored_count = 0
        skipped_count = 0

        for pdf_path in pdf_files:
            # Check if PDF already exists in database
            if PDFFile.objects.filter(file=f"pdfs/{pdf_path.name}").exists():
                self.stdout.write(f"Skipping {pdf_path.name} - already in database")
                skipped_count += 1
                continue

            try:
                # Extract text only (skip keywords for now)
                self.stdout.write(f"Processing {pdf_path.name}...")
                text_content = extract_text_from_pdf(str(pdf_path))
                
                # Create PDFFile object
                with open(pdf_path, 'rb') as f:
                    pdf_file = PDFFile(
                        title=pdf_path.stem.replace('_', ' ').title(),
                        uploaded_by=user,
                        folder=folder,
                        text_content=text_content,
                        keywords=[]  # Empty for now
                    )
                    pdf_file.file.save(pdf_path.name, File(f), save=True)

                self.stdout.write(
                    self.style.SUCCESS(
                        f"✓ Restored {pdf_path.name} - {len(text_content)} chars extracted"
                    )
                )
                restored_count += 1

            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f"✗ Failed to restore {pdf_path.name}: {str(e)}")
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"\nRestoration complete: {restored_count} PDFs restored, {skipped_count} skipped"
            )
        )
