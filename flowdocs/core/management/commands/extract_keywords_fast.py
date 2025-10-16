from django.core.management.base import BaseCommand
from core.models import PDFFile
from core.utils import extract_keywords
import re

class Command(BaseCommand):
    help = 'Extract keywords for PDFs efficiently (limited text length)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--max-chars',
            type=int,
            default=50000,  # Limit to 50k chars for faster processing
            help='Maximum characters to process per PDF (default: 50000)'
        )

    def handle(self, *args, **options):
        max_chars = options['max_chars']
        
        # Find PDFs that have text content but no keywords
        pdfs_with_text = PDFFile.objects.filter(
            text_content__isnull=False
        ).exclude(text_content='')
        
        pdfs_needing_keywords = pdfs_with_text.filter(keywords__isnull=True) | pdfs_with_text.filter(keywords=[])
        
        self.stdout.write(f"Found {pdfs_needing_keywords.count()} PDFs needing keyword extraction")
        self.stdout.write(f"Processing max {max_chars} characters per PDF for efficiency")

        processed_count = 0
        error_count = 0

        for pdf in pdfs_needing_keywords:
            try:
                self.stdout.write(f"Processing {pdf.title}...")
                
                # Truncate text for faster processing
                text_to_process = pdf.text_content[:max_chars]
                if len(pdf.text_content) > max_chars:
                    self.stdout.write(f"  Truncated from {len(pdf.text_content)} to {len(text_to_process)} chars")
                
                # Extract keywords from truncated text
                keywords = extract_keywords(text_to_process)
                
                # Update the PDF with keywords
                pdf.keywords = keywords
                pdf.save()
                
                self.stdout.write(
                    self.style.SUCCESS(
                        f"✓ Extracted {len(keywords)} keywords for {pdf.title}"
                    )
                )
                processed_count += 1

            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f"✗ Failed to extract keywords for {pdf.title}: {str(e)}")
                )
                error_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"\nKeyword extraction complete: {processed_count} PDFs processed, {error_count} errors"
            )
        )
