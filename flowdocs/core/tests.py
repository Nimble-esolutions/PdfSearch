import json
import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command, CommandError
from django.contrib.staticfiles import finders
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from .forms import UploadForm
from .data_release_validation import validate_release
from .management.commands.inventory_artifacts import build_manifest, compare_manifests
from .models import Folder, PDFFile
from .runtime_data_gate import RuntimeDataGateError, seed_pdf_media_report, validate_seed_pdf_media
from .runtime_config import validate_redis_url
from .utils import SearchDataIntegrityError, search_chunks_with_faiss_or_numpy


class StaticFilesConfigurationTests(TestCase):
    def test_core_static_asset_is_discovered_once(self):
        asset = 'main/css/style.css'
        matches = finders.find(asset, all=True)

        self.assertEqual(len(matches), 1)
        self.assertEqual(
            Path(matches[0]).resolve(),
            (settings.BASE_DIR / 'core' / 'static' / asset).resolve(),
        )


class OperationalEndpointTests(TestCase):
    def test_livez_is_process_only(self):
        response = self.client.get('/livez')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ok')

    def test_readyz_reports_dependencies(self):
        response = self.client.get('/readyz')
        self.assertIn(response.status_code, (200, 503))
        self.assertIn('checks', response.json())


class RedisConfigurationTests(SimpleTestCase):
    def test_accepts_compose_service_hostname(self):
        self.assertEqual(
            validate_redis_url("redis://redis:6379/1"),
            "redis://redis:6379/1",
        )

    def test_rejects_loopback_outside_local_development(self):
        with self.assertRaisesMessage(
            ImproperlyConfigured,
            "must not target loopback outside local development",
        ):
            validate_redis_url("redis://localhost:6379/1")

    def test_allows_loopback_for_local_development(self):
        self.assertEqual(
            validate_redis_url("redis://127.0.0.1:6379/1", allow_loopback=True),
            "redis://127.0.0.1:6379/1",
        )


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


class RegistrationSecurityTests(TestCase):
    def test_anonymous_registration_cannot_assign_privileged_role(self):
        response = self.client.get(reverse("register"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="role"')

        response = self.client.post(
            reverse("register"),
            {
                "username": "public-user",
                "email": "public@example.com",
                "password1": "A-strong-public-password-123!",
                "password2": "A-strong-public-password-123!",
                "department": "operations",
                "role": "superadmin",
            },
        )

        self.assertRedirects(response, reverse("login"))
        user = get_user_model().objects.get(username="public-user")
        self.assertEqual(user.role, "user")

    def test_admin_registration_preserves_privileged_role_selection(self):
        admin = get_user_model().objects.create_user(
            username="admin-creator",
            password="test-password",
            role="admin",
        )
        self.client.force_login(admin)

        response = self.client.get(reverse("register"))
        self.assertContains(response, 'name="role"')

        self.client.post(
            reverse("register"),
            {
                "username": "created-admin",
                "email": "created@example.com",
                "password1": "A-strong-created-password-123!",
                "password2": "A-strong-created-password-123!",
                "department": "admin",
                "role": "superadmin",
            },
        )

        self.assertEqual(
            get_user_model().objects.get(username="created-admin").role,
            "superadmin",
        )


class MutationAuthorizationTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(
            username="security-admin",
            password="test-password",
            role="admin",
        )
        self.superadmin = user_model.objects.create_user(
            username="security-superadmin",
            password="test-password",
            role="superadmin",
        )
        self.ordinary = user_model.objects.create_user(
            username="ordinary-user",
            password="test-password",
            role="user",
        )
        self.target = user_model.objects.create_user(
            username="target-user",
            password="test-password",
            role="user",
        )
        self.folder = Folder.objects.create(name="Security folder", created_by=self.admin)

    def test_ordinary_user_gets_403_for_user_and_folder_mutations(self):
        self.client.force_login(self.ordinary)

        self.assertEqual(self.client.get(reverse("user_list")).status_code, 403)
        self.assertEqual(
            self.client.post(reverse("toggle_user_status", args=[self.target.pk])).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(reverse("delete_user", args=[self.target.pk])).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(reverse("create_folder"), {"folder_name": "Nope"}).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                reverse("rename_folder", args=[self.folder.pk]),
                {"folder_name": "Nope"},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(reverse("delete_folder", args=[self.folder.pk])).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                reverse("update_folder_keywords", args=[self.folder.pk]),
                {"keywords": "nope"},
            ).status_code,
            403,
        )
        self.assertEqual(Folder.objects.filter(name="Nope").count(), 0)

    def test_get_requests_do_not_mutate_state_changing_endpoints(self):
        self.client.force_login(self.admin)
        original_active = self.target.is_active

        self.assertEqual(
            self.client.get(reverse("toggle_user_status", args=[self.target.pk])).status_code,
            405,
        )
        self.assertEqual(
            self.client.get(reverse("delete_user", args=[self.target.pk])).status_code,
            405,
        )
        self.assertEqual(
            self.client.get(reverse("delete_folder", args=[self.folder.pk])).status_code,
            405,
        )
        self.assertEqual(
            self.client.get(reverse("rename_folder", args=[self.folder.pk])).status_code,
            405,
        )
        self.assertEqual(
            self.client.get(reverse("create_folder")).status_code,
            405,
        )
        self.assertEqual(
            self.client.get(reverse("logout")).status_code,
            405,
        )
        self.assertEqual(self.target.is_active, original_active)
        self.assertTrue(Folder.objects.filter(pk=self.folder.pk).exists())
        self.assertTrue(get_user_model().objects.filter(pk=self.target.pk).exists())

    def test_csrf_is_required_for_destructive_post(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)

        response = csrf_client.post(reverse("delete_folder", args=[self.folder.pk]))

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Folder.objects.filter(pk=self.folder.pk).exists())

    def test_user_deletion_is_blocked_when_it_would_cascade_owned_content(self):
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            folder = Folder.objects.create(name="Owned folder", created_by=self.target)
            pdf = PDFFile.objects.create(
                title="Owned document",
                folder=folder,
                uploaded_by=self.target,
                file=SimpleUploadedFile("owned.pdf", b"%PDF-1.7\ncontent"),
            )
            self.client.force_login(self.superadmin)

            response = self.client.post(reverse("delete_user", args=[self.target.pk]))

            self.assertEqual(response.status_code, 409)
            self.assertTrue(get_user_model().objects.filter(pk=self.target.pk).exists())
            self.assertTrue(Folder.objects.filter(pk=folder.pk).exists())
            self.assertTrue(PDFFile.objects.filter(pk=pdf.pk).exists())


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

    def test_pdf_view_denies_an_ordinary_user_another_users_upload(self):
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            owner = get_user_model().objects.create_user(
                username="pdf-owner",
                password="test-password",
                role="admin",
            )
            viewer = get_user_model().objects.create_user(
                username="pdf-viewer",
                password="test-password",
                role="user",
            )
            pdf = PDFFile.objects.create(
                title="Private document",
                uploaded_by=owner,
                file=SimpleUploadedFile("private.pdf", b"%PDF-1.7\nprivate"),
            )
            self.client.force_login(viewer)

            response = self.client.get(reverse("view_pdf", args=[pdf.pk]))

            self.assertEqual(response.status_code, 403)

    def test_pdf_view_allows_an_ordinary_user_their_own_upload(self):
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            viewer = get_user_model().objects.create_user(
                username="own-pdf-viewer",
                password="test-password",
                role="user",
            )
            pdf = PDFFile.objects.create(
                title="Own document",
                uploaded_by=viewer,
                file=SimpleUploadedFile("own.pdf", b"%PDF-1.7\nown"),
            )
            self.client.force_login(viewer)

            response = self.client.get(reverse("view_pdf", args=[pdf.pk]))

            self.assertEqual(response.status_code, 200)
            self.assertEqual(b"".join(response.streaming_content), b"%PDF-1.7\nown")

    def test_delete_pdf_uses_dashboard_fallback_for_external_referer(self):
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            admin = get_user_model().objects.create_user(
                username="pdf-delete-admin",
                password="test-password",
                role="admin",
            )
            pdf = PDFFile.objects.create(
                title="Delete document",
                uploaded_by=admin,
                file=SimpleUploadedFile("delete.pdf", b"%PDF-1.7\ndelete"),
            )
            self.client.force_login(admin)

            response = self.client.post(
                reverse("delete_pdf", args=[pdf.pk]),
                HTTP_REFERER="https://evil.example/redirect",
            )

            self.assertRedirects(response, reverse("dashboard"))


class SearchAndAuthenticationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="search-admin",
            password="test-password",
            role="admin",
        )

    @patch("core.views.generate_gpt_answer", return_value="<b>unsafe</b>\nमराठी")
    @patch("core.views.search_pdfs_fast")
    @patch("core.views.detect_folder_by_keywords_multi")
    @patch("core.views.is_general_query", return_value=False)
    def test_search_references_use_protected_pdf_view_url(
        self,
        _is_general_query,
        detect_folder,
        search_pdfs,
        _generate_answer,
    ):
        folder = Folder.objects.create(name="Rules", created_by=self.user)
        pdf = PDFFile.objects.create(
            title="Rule book",
            file="pdfs/rule-book.pdf",
            folder=folder,
            uploaded_by=self.user,
        )
        self.client.force_login(self.user)
        detect_folder.return_value = [(folder, 0.9)]
        search_pdfs.return_value = (
            "",
            [{
                "title": pdf.title,
                "pdf_id": pdf.pk,
                "url": "/media/pdfs/rule-book.pdf",
                "folder": folder.name,
                "uploaded_at": "2026-07-22",
                "score": 1,
            }],
        )

        response = self.client.post(reverse("search_query"), {"query": "rule book"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["references"][0]["pdf_id"], pdf.pk)
        self.assertEqual(
            payload["references"][0]["url"],
            reverse("view_pdf", args=[pdf.pk]),
        )
        self.assertNotIn("/media/", payload["references"][0]["url"])
        self.assertEqual(payload["answer"], "<b>unsafe</b>\nमराठी")

    def test_anonymous_search_post_returns_json_401_without_document_content(self):
        response = self.client.post(
            reverse("search_query"),
            {"query": "private policy question"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"], "authentication_required")
        self.assertNotIn("private", response.content.decode())

    def test_search_folder_detection_is_scoped_to_visible_folders(self):
        folder = Folder.objects.create(name="Private rules", created_by=self.user)
        ordinary = get_user_model().objects.create_user(
            username="search-ordinary",
            password="test-password",
            role="user",
        )
        with patch(
            "core.views.detect_folder_by_keywords_multi",
            return_value=[],
        ) as detect_folder:
            self.client.force_login(ordinary)
            response = self.client.post(
                reverse("search_query"),
                {"query": "private policy question"},
            )

        self.assertEqual(response.status_code, 200)
        folders = detect_folder.call_args.kwargs["folders"]
        self.assertFalse(folders.filter(pk=folder.pk).exists())

    def test_login_preserves_safe_next_destination(self):
        next_url = reverse("dashboard_folder", args=[42])

        response = self.client.get(reverse("login"), {"next": next_url})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'value="{next_url}"')

        response = self.client.post(
            reverse("login"),
            {"username": self.user.username, "password": "test-password", "next": next_url},
        )

        self.assertRedirects(response, next_url, fetch_redirect_response=False)

    def test_login_rejects_external_next_destination(self):
        response = self.client.post(
            reverse("login"),
            {
                "username": self.user.username,
                "password": "test-password",
                "next": "https://evil.example/phishing",
            },
        )

        self.assertRedirects(response, reverse("dashboard"), fetch_redirect_response=False)

    def test_search_template_uses_text_rendering_and_resets_request_state(self):
        response = self.client.get(reverse("search_query"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "finally")
        self.assertContains(response, "textContent = ref.title")
        self.assertContains(response, "meta.textContent = [ref.folder, ref.uploaded_at]")
        self.assertContains(response, "AI-generated answers should not be used for legal purposes")
        self.assertNotContains(response, "innerHTML")
        self.assertNotContains(response, "|safe")


class RestorePDFCommandTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="restore-user",
            password="test-password",
            role="superadmin",
        )
        self.folder = Folder.objects.create(name="Restore", created_by=self.user)

    def test_restore_uses_configured_media_root_and_runs_pipeline(self):
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            pdf_path = Path(media_root) / "pdfs" / "configured.pdf"
            pdf_path.parent.mkdir()
            pdf_path.write_bytes(b"%PDF-1.7 configured")

            with patch("core.management.commands.restore_pdfs.precompute_pdf_embeddings") as pipeline:
                call_command("restore_pdfs", folder_id=self.folder.pk)

            restored = PDFFile.objects.get(file="pdfs/configured.pdf")
            self.assertEqual(restored.folder, self.folder)
            pipeline.assert_called_once_with(restored)

    def test_restore_fails_without_leaving_unsearchable_row(self):
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            pdf_path = Path(media_root) / "pdfs" / "broken.pdf"
            pdf_path.parent.mkdir()
            pdf_path.write_bytes(b"%PDF-1.7 broken")

            with patch(
                "core.management.commands.restore_pdfs.precompute_pdf_embeddings",
                side_effect=RuntimeError("embedding service unavailable"),
            ):
                with self.assertRaises(CommandError):
                    call_command("restore_pdfs", folder_id=self.folder.pk)

            self.assertFalse(PDFFile.objects.filter(file="pdfs/broken.pdf").exists())


class DashboardTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="dashboard-user",
            password="test-password",
            role="admin",
        )

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse("dashboard"))

        self.assertRedirects(response, f"{reverse('login')}?next={reverse('dashboard')}")

    def test_authenticated_dashboard_renders_empty_folder_list(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Categories")

    def test_dashboard_renders_flash_messages_with_accessible_dismissal(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("create_folder"),
            {"folder_name": "Visible feedback", "folder_keywords": "feedback"},
            follow=True,
        )

        self.assertContains(response, "Visible feedback")
        self.assertContains(response, 'id="flash-messages"')
        self.assertContains(response, 'class="btn-close"')
        self.assertContains(response, 'aria-label="Close"')

    def test_upload_failure_rolls_back_row_and_stored_file(self):
        folder = Folder.objects.create(name="Upload failures", created_by=self.user)
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            with patch(
                "core.views.precompute_pdf_embeddings",
                side_effect=RuntimeError("embedding service unavailable"),
            ):
                self.client.force_login(self.user)
                response = self.client.post(
                    reverse("dashboard_folder", args=[folder.pk]),
                    {
                        "title": "Unavailable embedding service",
                        "file": SimpleUploadedFile(
                            "unavailable.pdf",
                            b"%PDF-1.7\nvalid upload fixture",
                            content_type="application/pdf",
                        ),
                    },
                )

            self.assertEqual(response.status_code, 400)
            self.assertContains(response, "No document was saved", status_code=400)
            self.assertFalse(
                PDFFile.objects.filter(title="Unavailable embedding service").exists()
            )
            self.assertFalse(any(path.is_file() for path in Path(media_root).rglob("*")))

    def test_malformed_upload_is_rejected_before_persistence(self):
        folder = Folder.objects.create(name="Malformed uploads", created_by=self.user)
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("dashboard_folder", args=[folder.pk]),
            {
                "title": "Malformed PDF",
                "file": SimpleUploadedFile(
                    "malformed.pdf",
                    b"not a PDF",
                    content_type="application/pdf",
                ),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "valid PDF")
        self.assertFalse(PDFFile.objects.filter(title="Malformed PDF").exists())

    def test_dashboard_renders_many_folders_with_current_navigation_route(self):
        self.client.force_login(self.user)
        folders = [
            Folder(name=f"Folder {index:02d}", created_by=self.user)
            for index in range(25)
        ]
        Folder.objects.bulk_create(folders)

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("dashboard_folder", args=[folders[0].pk]))
        self.assertContains(response, "Folder 24")

    def test_folder_navigation_renders_pdfs_and_nullable_uploader(self):
        self.client.force_login(self.user)
        folder = Folder.objects.create(name="Documents", created_by=self.user)
        pdf = PDFFile.objects.create(
            title="Legacy document",
            file="pdfs/legacy-document.pdf",
            folder=folder,
            uploaded_by=None,
        )

        response = self.client.get(reverse("dashboard_folder", args=[folder.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, pdf.title)
        self.assertContains(response, "Unknown")
        self.assertContains(response, reverse("view_pdf", args=[pdf.pk]))
        self.assertContains(response, reverse("dashboard_folder", args=[folder.pk]))

    def test_pdf_rename_redirects_back_to_its_folder(self):
        self.client.force_login(self.user)
        folder = Folder.objects.create(name="Documents", created_by=self.user)
        pdf = PDFFile.objects.create(
            title="Before rename",
            file="pdfs/document.pdf",
            folder=folder,
            uploaded_by=self.user,
        )

        response = self.client.post(
            reverse("rename_pdf", args=[pdf.pk]),
            {"title": "After rename"},
        )

        self.assertRedirects(
            response,
            reverse("dashboard_folder", args=[folder.pk]),
        )
        self.assertEqual(PDFFile.objects.get(pk=pdf.pk).title, "After rename")


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

    def test_comparison_classifies_database_hash_difference(self):
        source = build_manifest(self._root_with_pdf(root=Path(self.temp_dir.name) / 'database-source'))
        target = build_manifest(self._root_with_pdf(root=Path(self.temp_dir.name) / 'database-target'))
        target['database']['sha256'] = '0' * 64

        result = compare_manifests(source, target)

        self.assertEqual(result['counts']['database_hash_differences'], 1)
        self.assertEqual(
            result['classifications']['database_hash_differences'][0]['field'],
            'database.sha256',
        )

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

    @staticmethod
    def _policy(manifest, **overrides):
        policy = {
            'pdf_rows': manifest['counts']['pdf_rows'],
            'pdf_storage_files': manifest['counts']['pdf_storage_files'],
            'faiss_files': manifest['counts']['faiss_files'],
            'faiss_vectors': 0,
            'preserved_target_only_rows': manifest['counts']['pdf_rows_missing_files'],
        }
        policy.update(overrides)
        manifest['expected_counts'] = policy
        return manifest

    def test_release_validation_accepts_matching_read_only_fixture(self):
        root = self._root_with_pdf()
        manifest = self._policy(build_manifest(root))

        before = (root / 'db.sqlite3').read_bytes()
        report = validate_release(manifest, root)

        self.assertTrue(report['ok'], report['issues'])
        self.assertEqual(before, (root / 'db.sqlite3').read_bytes())

    def test_release_validation_requires_explicit_count_policy(self):
        root = self._root_with_pdf()
        manifest = build_manifest(root)

        report = validate_release(manifest, root)

        self.assertFalse(report['ok'])
        self.assertIn('expected-count-missing', {issue['kind'] for issue in report['issues']})

    def test_release_validation_rejects_missing_pdf(self):
        root = self._root_with_pdf()
        manifest = self._policy(build_manifest(root), preserved_target_only_rows=0)
        (root / 'media' / 'pdfs' / 'document.pdf').unlink()

        report = validate_release(manifest, root)

        self.assertFalse(report['ok'])
        self.assertIn('pdf-file_status-mismatch', {issue['kind'] for issue in report['issues']})
        self.assertIn('expected-count-mismatch', {issue['kind'] for issue in report['issues']})

    def test_release_validation_rejects_migration_leaf_mismatch(self):
        root = self._root_with_pdf()
        manifest = self._policy(build_manifest(root))
        manifest['database']['migrations']['latest'] = '0011_previous'

        report = validate_release(manifest, root)

        self.assertFalse(report['ok'])
        self.assertIn('migration-leaf-mismatch', {issue['kind'] for issue in report['issues']})

    def test_release_validation_rejects_faiss_metadata_mismatch(self):
        root = self._root_with_pdf()
        faiss_path = root / 'faiss_indexes' / 'folder_7.index'
        faiss_path.parent.mkdir(parents=True)
        faiss_path.write_bytes(b'fixture-index')
        manifest = self._policy(build_manifest(root), faiss_files=1, faiss_vectors=2)
        manifest['faiss']['files'][0]['faiss'] = {
            'loadable': True,
            'dimensions': 1536,
            'vector_count': 2,
        }

        with patch(
            'core.data_release_validation.inspect_faiss_file',
            return_value={'loadable': True, 'dimensions': 768, 'vector_count': 2},
        ):
            report = validate_release(manifest, root)

        self.assertFalse(report['ok'])
        self.assertIn('faiss-dimensions-mismatch', {issue['kind'] for issue in report['issues']})

    def test_release_validation_preserves_expected_target_only_rows(self):
        root = Path(self.temp_dir.name) / 'preserved-target'
        media = root / 'media' / 'pdfs'
        media.mkdir(parents=True)
        (media / 'present.pdf').write_bytes(b'%PDF-1.7\npresent')
        connection = sqlite3.connect(root / 'db.sqlite3')
        connection.executescript(
            'CREATE TABLE django_migrations (app varchar(255), name varchar(255));'
            'CREATE TABLE core_pdffile ('
            'id integer primary key, title varchar(200), file varchar(100), '
            'extracted_text text, page_chunks text, chunk_embeddings text, '
            'text_content text, indexed bool);'
            "INSERT INTO django_migrations VALUES ('core', '0012_latest');"
            "INSERT INTO core_pdffile VALUES "
            "(1, 'Preserved target row', 'pdfs/target-only.pdf', NULL, NULL, NULL, NULL, 0);"
            "INSERT INTO core_pdffile VALUES "
            "(2, 'Present row', 'pdfs/present.pdf', NULL, NULL, NULL, NULL, 1);"
        )
        connection.commit()
        connection.close()
        manifest = self._policy(build_manifest(root), preserved_target_only_rows=1)

        report = validate_release(manifest, root)

        self.assertTrue(report['ok'], report['issues'])
        self.assertEqual(report['checks']['counts']['pdf_rows'], 2)

    def test_inventory_covers_configured_chroma_and_static_trees(self):
        root = self._root_with_pdf()
        (root / 'chroma_db').mkdir()
        (root / 'chroma_db' / 'index.sqlite3').write_bytes(b'chroma')
        (root / 'staticfiles').mkdir()
        (root / 'staticfiles' / 'app.css').write_bytes(b'css')

        manifest = build_manifest(root)

        self.assertEqual(manifest['chroma']['contract'], 'in-contract')
        self.assertEqual(manifest['chroma']['file_count'], 1)
        self.assertEqual(manifest['static']['file_count'], 1)
        self.assertEqual(manifest['counts']['chroma_files'], 1)
        self.assertEqual(manifest['counts']['static_files'], 1)

    def test_release_validation_rejects_database_hash_drift(self):
        root = self._root_with_pdf()
        manifest = self._policy(build_manifest(root))
        connection = sqlite3.connect(root / 'db.sqlite3')
        connection.execute("UPDATE core_pdffile SET title = 'Changed'")
        connection.commit()
        connection.close()

        report = validate_release(manifest, root)

        self.assertFalse(report['ok'])
        self.assertIn('database-sha256-mismatch', {issue['kind'] for issue in report['issues']})

    def test_runtime_seed_gate_rejects_missing_media(self):
        root = Path(self.temp_dir.name) / 'seed-gate'
        root.mkdir()
        connection = sqlite3.connect(root / 'db.sqlite3')
        connection.executescript(
            'CREATE TABLE core_pdffile (id integer primary key, file varchar(100));'
            "INSERT INTO core_pdffile VALUES (1, 'pdfs/missing.pdf');"
        )
        connection.commit()
        connection.close()

        report = seed_pdf_media_report(root / 'db.sqlite3', root / 'media')
        self.assertEqual(report['pdf_rows'], 1)
        self.assertEqual(len(report['missing_media']), 1)
        with self.assertRaises(RuntimeDataGateError):
            validate_seed_pdf_media(root / 'db.sqlite3', root / 'media')

        media = root / 'media' / 'pdfs'
        media.mkdir(parents=True)
        (media / 'missing.pdf').write_bytes(b'%PDF-1.7')
        self.assertEqual(validate_seed_pdf_media(root / 'db.sqlite3', root / 'media')['pdf_rows'], 1)

    def test_search_rejects_faiss_vector_count_mismatch(self):
        fake_index = SimpleNamespace(d=2, ntotal=2)
        with patch('core.utils._HAS_FAISS', True):
            with self.assertRaises(SearchDataIntegrityError):
                search_chunks_with_faiss_or_numpy(
                    np.array([1.0, 0.0], dtype=np.float32),
                    fake_index,
                    ['chunk'],
                    embeddings_matrix=np.array([[1.0, 0.0]], dtype=np.float32),
                )

    def test_search_rejects_faiss_dimension_mismatch(self):
        fake_index = SimpleNamespace(d=3, ntotal=1)
        with patch('core.utils._HAS_FAISS', True):
            with self.assertRaises(SearchDataIntegrityError):
                search_chunks_with_faiss_or_numpy(
                    np.array([1.0, 0.0], dtype=np.float32),
                    fake_index,
                    ['chunk'],
                    embeddings_matrix=np.array([[1.0, 0.0]], dtype=np.float32),
                )
