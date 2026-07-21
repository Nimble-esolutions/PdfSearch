from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from .forms import UploadForm


class OperationalEndpointTests(TestCase):
    def test_livez_is_process_only(self):
        response = self.client.get('/livez')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ok')

    def test_readyz_reports_dependencies(self):
        response = self.client.get('/readyz')
        self.assertIn(response.status_code, (200, 503))
        self.assertIn('checks', response.json())


class UploadValidationTests(TestCase):
    def test_rejects_non_pdf_content(self):
        uploaded = SimpleUploadedFile('payload.pdf', b'not a pdf', content_type='application/pdf')
        form = UploadForm(data={'title': 'Payload'}, files={'file': uploaded})
        self.assertFalse(form.is_valid())
        self.assertIn('valid PDF', str(form.errors))

    def test_accepts_pdf_signature(self):
        uploaded = SimpleUploadedFile('document.pdf', b'%PDF-1.7\ncontent', content_type='application/pdf')
        form = UploadForm(data={'title': 'Document'}, files={'file': uploaded})
        self.assertTrue(form.is_valid(), form.errors)
