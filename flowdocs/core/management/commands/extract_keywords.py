from django.core.management.base import BaseCommand
from core.models import PDFFile
from core.utils import extract_keywords

class Command(BaseCommand):
    help = 'Extract keywords for PDFs that have text content but no keywords'

    def handle(self, *args, **options):
        # Find PDFs that have text content but no keywords
        pdfs_with_text = PDFFile.objects.filter(
            text_content__isnull=False
        ).exclude(text_content='')
        
        pdfs_needing_keywords = pdfs_with_text.filter(keywords__isnull=True) | pdfs_with_text.filter(keywords=[])
        
        self.stdout.write(f"Found {pdfs_needing_keywords.count()} PDFs needing keyword extraction")

        processed_count = 0
        error_count = 0

        for pdf in pdfs_needing_keywords:
            try:
                self.stdout.write(f"Processing {pdf.title}...")
                
                # Extract keywords
                keywords = extract_keywords(pdf.text_content)
                
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
