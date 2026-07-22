import json
import sqlite3
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .forms import UploadForm
from .management.commands.inventory_artifacts import build_manifest, compare_manifests
from .models import PDFFile


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


class PDFViewTests(TestCase):
    def test_pdf_view_requires_login(self):
        response = self.client.get(reverse("view_pdf", args=[1]))
        self.assertEqual(response.status_code, 302)

    def test_pdf_view_serves_file_to_authenticated_user(self):
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            user = get_user_model().objects.create_user(
                username="viewer",
                password="test-password",
                role="admin",
            )
            pdf = PDFFile.objects.create(
                title="Document",
                uploaded_by=user,
                file=SimpleUploadedFile("document.pdf", b"%PDF-1.7\ncontent"),
            )
            self.client.force_login(user)
            response = self.client.get(reverse("view_pdf", args=[pdf.pk]))

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Content-Type"], "application/pdf")
            self.assertEqual(b"".join(response.streaming_content), b"%PDF-1.7\ncontent")


class ArtifactInventoryTests(TestCase):
    def _root_with_pdf(self, content=b'%PDF-1.7\ncontent', root=None):
        root = Path(root or self.temp_dir.name)
        media = root / 'media' / 'pdfs'
        media.mkdir(parents=True)
        (media / 'document.pdf').write_bytes(content)
        connection = sqlite3.connect(root / 'db.sqlite3')
        connection.executescript(
            'CREATE TABLE django_migrations (app varchar(255), name varchar(255));'
            'CREATE TABLE core_pdffile ('
            'id integer primary key, title varchar(200), file varchar(100), '
            'extracted_text text, page_chunks text, chunk_embeddings text, '
            'text_content text, indexed bool);'
            "INSERT INTO django_migrations VALUES ('core', '0012_latest');"
            "INSERT INTO core_pdffile VALUES "
            "(1, 'Document', 'pdfs/document.pdf', 'private text', '[\"chunk\"]', "
            "'[[0.1, 0.2]]', 'private text', 1);"
        )
        connection.commit()
        connection.close()
        return root

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_inventory_is_deterministic_and_does_not_include_pdf_contents(self):
        root = self._root_with_pdf()
        first = build_manifest(root)
        second = build_manifest(root)

        self.assertEqual(
            json.dumps(first, sort_keys=True),
            json.dumps(second, sort_keys=True),
        )
        self.assertEqual(first['counts']['pdf_rows'], 1)
        self.assertEqual(first['pdfs'][0]['sha256'], first['pdf_storage']['files'][0]['sha256'])
        self.assertNotIn('private text', json.dumps(first))
        self.assertEqual(first['pdfs'][0]['metadata']['embedding_dimensions'], [2])

    def test_inventory_marks_missing_pdf_without_hash(self):
        root = self._root_with_pdf()
        (root / 'media' / 'pdfs' / 'document.pdf').unlink()

        pdf = build_manifest(root)['pdfs'][0]

        self.assertFalse(pdf['exists'])
        self.assertEqual(pdf['file_status'], 'missing')
        self.assertIsNone(pdf['sha256'])

    def test_comparison_classifies_hash_difference(self):
        source_root = self._root_with_pdf(b'one', Path(self.temp_dir.name) / 'source')
        target_root = self._root_with_pdf(b'two', Path(self.temp_dir.name) / 'target')

        result = compare_manifests(build_manifest(source_root), build_manifest(target_root))

        self.assertEqual(result['counts']['path_hash_conflicts'], 1)
        self.assertEqual(result['counts']['exact_matches'], 0)

    def test_comparison_classifies_schema_and_migration_difference(self):
        source = build_manifest(self._root_with_pdf(root=Path(self.temp_dir.name) / 'schema-source'))
        target = build_manifest(self._root_with_pdf(root=Path(self.temp_dir.name) / 'schema-target'))
        target['schema']['pdf_model'] = 'core.LegacyPDFFile'
        target['database']['migrations']['latest'] = '0009_legacy'

        differences = compare_manifests(source, target)['classifications']['schema_migration_differences']

        self.assertEqual(
            [difference['field'] for difference in differences],
            ['database.migrations.latest', 'schema.pdf_model'],
        )
