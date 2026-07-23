import json
import os
import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import skipUnless
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
from django.utils import translation

from flowdocs import settings as project_settings

from .forms import UploadForm
from . import utils as core_utils
from .data_release_validation import validate_release
from .management.commands.inventory_artifacts import build_manifest, compare_manifests
from .models import Folder, PDFFile, MaintenanceJob, MaintenanceAuditEvent, ArtifactGeneration, ArtifactValidation
from .maintenance import run_job, queue_job, _audit, _record_validation, promote_active_generation, rollback_to_generation, purge_generation, purge_expired_generations
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


class LanguageAndPublicUiTests(TestCase):
    def test_public_search_defaults_to_english_with_help_link_and_footer(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["welcome_message"],
            "I am Sahakar AI. Click here to learn how to questions to get correct answers.",
        )
        self.assertEqual(response.context["welcome_help_label"], "Click Here")
        self.assertContains(response, "All rights reserved© Registrar Co-operative Societies.")
        self.assertContains(response, 'name="language" value="mr"')
        self.assertContains(response, '<html lang="en">')

    def test_language_switch_renders_marathi_greeting_and_english_return(self):
        response = self.client.post(
            reverse("set_language"),
            {"language": "mr", "next": reverse("home")},
        )

        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("मी सहकार AI", response.context["welcome_message"])
        self.assertEqual(response.context["welcome_help_label"], "इथे क्लिक करा")
        self.assertContains(response, 'name="language" value="en"')
        self.assertContains(response, '<html lang="mr">')

    def test_language_catalog_is_available_for_runtime_translation(self):
        with translation.override("mr"):
            self.assertEqual(
                translation.gettext("Click Here"),
                "इथे क्लिक करा",
            )


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


class EnvironmentContractTests(SimpleTestCase):
    def test_positive_int_reader_accepts_env_override(self):
        with patch.dict(os.environ, {"PDF_CHUNK_SIZE": "2048"}):
            self.assertEqual(
                project_settings._env_positive_int("PDF_CHUNK_SIZE", 1200),
                2048,
            )

    def test_positive_int_reader_rejects_invalid_value(self):
        with patch.dict(os.environ, {"PDF_CHUNK_SIZE": "0"}):
            with self.assertRaisesMessage(
                ImproperlyConfigured,
                "PDF_CHUNK_SIZE must be a positive integer",
            ):
                project_settings._env_positive_int("PDF_CHUNK_SIZE", 1200)


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
    def test_anonymous_registration_is_not_public(self):
        response = self.client.get(reverse("register"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

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

        self.assertEqual(response.status_code, 302)
        self.assertFalse(get_user_model().objects.filter(username="public-user").exists())

    def test_ordinary_authenticated_user_cannot_register_accounts(self):
        user = get_user_model().objects.create_user(
            username="ordinary-user",
            password="test-password",
            role="user",
        )
        self.client.force_login(user)
        response = self.client.get(reverse("register"))
        self.assertEqual(response.status_code, 403)

    def test_admin_registration_cannot_grant_superadmin(self):
        admin = get_user_model().objects.create_user(
            username="admin-creator",
            password="test-password",
            role="admin",
        )
        self.client.force_login(admin)

        response = self.client.get(reverse("register"))
        self.assertContains(response, 'name="role"')
        self.assertContains(response, 'class="admin-shell"')
        self.assertContains(response, "Dashboard")

        response = self.client.post(
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

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select a valid choice")
        self.assertFalse(get_user_model().objects.filter(username="created-admin").exists())

    def test_superadmin_registration_can_grant_superadmin(self):
        superadmin = get_user_model().objects.create_user(
            username="superadmin-creator",
            password="test-password",
            role="superadmin",
        )
        self.client.force_login(superadmin)
        response = self.client.post(
            reverse("register"),
            {
                "username": "created-superadmin",
                "email": "created@example.com",
                "password1": "A-strong-created-password-123!",
                "password2": "A-strong-created-password-123!",
                "department": "admin",
                "role": "superadmin",
            },
        )
        self.assertRedirects(response, reverse("login"))
        self.assertEqual(
            get_user_model().objects.get(username="created-superadmin").role,
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
            department="operations",
        )
        self.folder = Folder.objects.create(name="Security folder", created_by=self.admin)

    def test_ordinary_user_gets_403_for_user_and_folder_mutations(self):
        self.client.force_login(self.ordinary)
        pdf = PDFFile.objects.create(
            title="Unknown owner",
            file="pdfs/unknown-owner.pdf",
            folder=self.folder,
            uploaded_by=None,
        )

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
        self.assertEqual(
            self.client.post(
                reverse("assign_pdf_owner", args=[pdf.pk]),
                {"owner_id": self.target.pk},
            ).status_code,
            403,
        )
        self.assertEqual(Folder.objects.filter(name="Nope").count(), 0)
        pdf.refresh_from_db()
        self.assertIsNone(pdf.uploaded_by)

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
        pdf = PDFFile.objects.create(
            title="Unknown owner",
            file="pdfs/unknown-owner.pdf",
            folder=self.folder,
            uploaded_by=None,
        )
        self.assertEqual(
            self.client.get(reverse("assign_pdf_owner", args=[pdf.pk])).status_code,
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

    def test_user_list_renders_status_and_toggle_action(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("user_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Users & Access")
        self.assertContains(response, "Account directory")
        self.assertContains(response, "Active")
        self.assertContains(response, "Current user")
        self.assertContains(response, reverse("toggle_user_status", args=[self.target.pk]))
        self.assertContains(response, "Deactivate")

    def test_user_list_filters_by_search_role_department_and_status(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("user_list"),
            {"q": "target", "role": "user", "department": "operations", "status": "active"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "target-user")
        self.assertNotContains(response, "<strong>security-admin</strong>")

    def test_admin_cannot_edit_superadmin_or_deactivate_last_superadmin(self):
        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.get(reverse("edit_user", args=[self.superadmin.pk])).status_code,
            403,
        )
        self.client.force_login(self.superadmin)
        response = self.client.post(reverse("toggle_user_status", args=[self.superadmin.pk]))
        self.assertRedirects(response, reverse("user_list"))
        self.superadmin.refresh_from_db()
        self.assertTrue(self.superadmin.is_active)

    def test_superadmin_can_edit_user_access_details(self):
        self.client.force_login(self.superadmin)
        response = self.client.post(
            reverse("edit_user", args=[self.target.pk]),
            {"email": "updated@example.com", "department": "finance", "role": "admin"},
        )
        self.assertRedirects(response, reverse("user_list"))
        self.target.refresh_from_db()
        self.assertEqual(self.target.email, "updated@example.com")
        self.assertEqual(self.target.department, "finance")
        self.assertEqual(self.target.role, "admin")


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

    def test_anonymous_search_post_is_allowed_without_document_content(self):
        response = self.client.post(
            reverse("search_query"),
            {"query": "private policy question"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("private", response.content.decode())

    def test_anonymous_search_uses_approved_public_scope(self):
        folder = Folder.objects.create(name="Public rules", created_by=self.user)
        pdf = PDFFile.objects.create(
            title="Public rule book",
            file=SimpleUploadedFile("public-rule.pdf", b"%PDF-1.7 public"),
            folder=folder,
            uploaded_by=self.user,
            indexed=True,
            page_chunks=["Public rule content"],
        )

        with override_settings(
            PUBLIC_SEARCH_ENABLED=True,
            PUBLIC_SEARCH_ALL_FOLDERS=True,
            PUBLIC_SEARCH_FOLDER_IDS=frozenset(),
        ), patch(
            "core.views.is_general_query",
            return_value=False,
        ), patch(
            "core.views.detect_folder_by_keywords_multi",
            return_value=[(folder, 0.9)],
        ), patch(
            "core.views.search_pdfs_fast",
            return_value=(
                "",
                [{
                    "title": pdf.title,
                    "pdf_id": pdf.pk,
                    "folder": folder.name,
                    "uploaded_at": "2026-07-22",
                    "score": 1,
                }],
            ),
        ), patch(
            "core.views.generate_gpt_answer",
            return_value="Public answer",
        ):
            response = self.client.post(
                reverse("search_query"),
                {"query": "public rule"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], "Public answer")
        self.assertEqual(
            response.json()["references"][0]["url"],
            reverse("public_view_pdf", args=[pdf.pk]),
        )
        self.assertNotIn("/media/", response.json()["references"][0]["url"])

        with override_settings(
            PUBLIC_SEARCH_ENABLED=True,
            PUBLIC_SEARCH_ALL_FOLDERS=True,
            PUBLIC_SEARCH_FOLDER_IDS=frozenset(),
        ):
            public_pdf = self.client.get(reverse("public_view_pdf", args=[pdf.pk]))
            self.assertEqual(public_pdf.status_code, 200)
            self.assertEqual(public_pdf["Content-Type"], "application/pdf")

    def test_root_head_request_is_successful(self):
        response = self.client.head("/")

        self.assertEqual(response.status_code, 200)

    def test_search_rejects_queries_over_server_word_limit(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("search_query"),
            {"query": "word " * 31},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "query_too_long")

    def test_search_integrity_failure_returns_service_unavailable(self):
        folder = Folder.objects.create(name="Act", created_by=self.user)
        self.client.force_login(self.user)

        with patch(
            "core.views.is_general_query",
            return_value=False,
        ), patch(
            "core.views.detect_folder_by_keywords_multi",
            return_value=[(folder, 0.9)],
        ), patch(
            "core.views.search_pdfs_fast",
            side_effect=SearchDataIntegrityError("stale index"),
        ) as search_pdfs:
            response = self.client.post(
                reverse("search_query"),
                {"query": "act search"},
            )

        search_pdfs.assert_called_once()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"], "search_unavailable")
        self.assertNotIn("stale index", response.content.decode())


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
        self.assertContains(response, 'class="admin-shell"')
        self.assertContains(response, 'id="togglePassword"')
        self.assertContains(response, 'aria-pressed="false"')

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
        self.assertContains(response, "errorMessages")
        self.assertContains(response, "तात्पुरती अनुपलब्ध")
        self.assertContains(response, "safeReferenceUrl(ref)")
        self.assertContains(response, "textContent = ref.title")
        self.assertContains(response, "meta.textContent = [ref.folder, ref.uploaded_at]")
        self.assertContains(response, "AI-generated answers should not be used for legal purposes")
        self.assertContains(response, 'rel="noopener noreferrer"')
        self.assertNotContains(response, "innerHTML")
        self.assertNotContains(response, "|safe")


class SearchIndexLifecycleTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="index-owner",
            password="test-password",
            role="admin",
        )
        self.folder = Folder.objects.create(name="Index lifecycle", created_by=self.user)

    def test_invalid_pdf_metadata_does_not_poison_valid_folder_search(self):
        PDFFile.objects.create(
            title="Valid searchable PDF",
            file="pdfs/valid.pdf",
            folder=self.folder,
            uploaded_by=self.user,
            page_chunks=["A valid searchable chunk"],
            chunk_embeddings=[[1.0, 0.0]],
        )
        PDFFile.objects.create(
            title="Incomplete legacy PDF",
            file="pdfs/incomplete.pdf",
            folder=self.folder,
            uploaded_by=self.user,
        )

        chunks, matrix = core_utils._folder_embedding_matrix(self.folder)

        self.assertEqual(chunks, ["A valid searchable chunk"])
        self.assertEqual(matrix.shape, (1, 2))

    @skipUnless(core_utils._HAS_FAISS, "FAISS is required for atomic index tests")
    def test_failed_rebuild_preserves_existing_index(self):
        with tempfile.TemporaryDirectory() as index_dir, override_settings(FAISS_INDEX_DIR=index_dir):
            index_path = core_utils.faiss_index_path_for_folder(self.folder)
            old_index = core_utils.faiss.IndexFlatIP(2)
            old_index.add(np.array([[1.0, 0.0]], dtype=np.float32))
            core_utils.faiss.write_index(old_index, index_path)

            with patch.object(
                core_utils,
                "_folder_embedding_matrix",
                side_effect=SearchDataIntegrityError("rebuild failed"),
            ):
                with self.assertRaises(SearchDataIntegrityError):
                    core_utils.build_or_load_faiss_index_for_folder(
                        self.folder,
                        force_rebuild=True,
                    )

            preserved = core_utils.faiss.read_index(index_path)
            self.assertEqual(preserved.ntotal, 1)


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
        self.assertContains(response, "Operations Cockpit")
        self.assertContains(response, "Dispatch Board")
        self.assertContains(response, "Index Debt Queue")
        self.assertNotContains(response, "Safety Gates")

    def test_dashboard_renders_marathi_cockpit_labels(self):
        self.client.force_login(self.user)
        Folder.objects.create(name="Marathi lane", created_by=self.user)
        self.client.post(
            reverse("set_language"),
            {"language": "mr", "next": reverse("dashboard")},
        )

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "संचालन नियंत्रण कक्ष")
        self.assertContains(response, "श्रेणी कार्यक्षेत्र")
        self.assertContains(response, "प्रेषण फलक")
        self.assertContains(response, "अनुक्रमण थकबाकी रांग")
        self.assertContains(response, "रिकामा विभाग")

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
        self.assertContains(response, "Category Yard")
        self.assertContains(response, "Search readiness")
        self.assertContains(response, 'aria-label="Close"', count=26)
        self.assertContains(response, "Delete this category and all PDFs inside it")

    def test_dashboard_index_debt_queue_links_actionable_categories(self):
        self.client.force_login(self.user)
        index_folder = Folder.objects.create(name="Needs index lane", created_by=self.user)
        PDFFile.objects.create(
            title="Needs index",
            file="pdfs/needs-index.pdf",
            folder=index_folder,
            uploaded_by=self.user,
            indexed=False,
        )
        owner_folder = Folder.objects.create(name="Owner review lane", created_by=self.user)
        PDFFile.objects.create(
            title="Unknown owner",
            file="pdfs/unknown-owner.pdf",
            folder=owner_folder,
            uploaded_by=None,
            indexed=True,
        )
        empty_folder = Folder.objects.create(name="Empty lane", created_by=self.user)

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Index Debt Queue")
        self.assertContains(response, "Needs index lane")
        self.assertContains(response, "Index review")
        self.assertContains(response, "Owner review lane")
        self.assertContains(response, "Owner review")
        self.assertContains(response, "Empty lane")
        self.assertContains(response, "Empty bay")
        self.assertContains(response, reverse("dashboard_folder", args=[index_folder.pk]))
        self.assertContains(response, reverse("dashboard_folder", args=[owner_folder.pk]))
        self.assertContains(response, reverse("dashboard_folder", args=[empty_folder.pk]))
        self.assertNotContains(response, "Prefer rename or quarantine over delete")

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
        self.assertContains(response, "Edit Keywords")
        self.assertContains(response, "Document Workbench")
        self.assertContains(response, "Blast Radius")
        self.assertContains(response, 'rel="noopener noreferrer"')
        self.assertContains(response, "Assign Owner")
        self.assertContains(response, reverse("assign_pdf_owner", args=[pdf.pk]))
        self.assertContains(response, 'aria-label="Close"', count=3)
        self.assertContains(response, "Superadmin access is required")
        self.assertNotContains(response, "Repair Stored Index")

    def test_admin_can_assign_owner_to_unknown_owner_pdf(self):
        owner = get_user_model().objects.create_user(
            username="new-owner",
            password="test-password",
            role="admin",
        )
        self.client.force_login(self.user)
        folder = Folder.objects.create(name="Documents", created_by=self.user)
        pdf = PDFFile.objects.create(
            title="Unknown owner document",
            file="pdfs/unknown-owner.pdf",
            folder=folder,
            uploaded_by=None,
        )

        response = self.client.post(
            reverse("assign_pdf_owner", args=[pdf.pk]),
            {"owner_id": owner.pk},
            follow=True,
        )

        self.assertRedirects(response, reverse("dashboard_folder", args=[folder.pk]))
        pdf.refresh_from_db()
        self.assertEqual(pdf.uploaded_by, owner)
        self.assertContains(response, "assigned to new-owner")
        self.assertNotContains(response, "Owner review")

    def test_folder_blast_radius_shows_superadmin_index_controls(self):
        superadmin = get_user_model().objects.create_user(
            username="ops-superadmin",
            password="test-password",
            role="superadmin",
        )
        folder = Folder.objects.create(name="Documents", created_by=superadmin)
        PDFFile.objects.create(
            title="Needs repair",
            file="pdfs/needs-repair.pdf",
            folder=folder,
            uploaded_by=superadmin,
            indexed=False,
        )
        self.client.force_login(superadmin)

        response = self.client.get(reverse("dashboard_folder", args=[folder.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Index Operations")
        self.assertContains(response, "Repair Stored Index")
        self.assertContains(response, "Reprocess Needed")
        self.assertContains(response, "Reprocess All")

    def test_folder_operations_requires_superadmin(self):
        folder = Folder.objects.create(name="Documents", created_by=self.user)
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("folder_operations", args=[folder.pk]),
            {"operation": "repair_stored_index"},
        )

        self.assertEqual(response.status_code, 403)

    def test_superadmin_can_cancel_and_retry_maintenance_job(self):
        superadmin = get_user_model().objects.create_user(
            username="maintenance-superadmin",
            password="test-password",
            role="superadmin",
        )
        self.client.force_login(superadmin)
        queued = MaintenanceJob.objects.create(kind="validate", status="queued")

        response = self.client.post(
            reverse("maintenance_job_action", args=[queued.public_id]),
            {"action": "cancel"},
        )

        self.assertRedirects(response, reverse("dashboard"))
        queued.refresh_from_db()
        self.assertEqual(queued.status, "cancelled")

        failed = MaintenanceJob.objects.create(
            kind="validate", status="failed", failed_items=1, error_summary="old error"
        )
        response = self.client.post(
            reverse("maintenance_job_action", args=[failed.public_id]),
            {"action": "retry"},
        )

        self.assertRedirects(response, reverse("dashboard"))
        failed.refresh_from_db()
        self.assertEqual(failed.status, "queued")
        self.assertEqual(failed.failed_items, 0)
        self.assertEqual(failed.error_summary, "")

    def test_superadmin_can_repair_index_from_stored_artifacts(self):
        superadmin = get_user_model().objects.create_user(
            username="repair-superadmin",
            password="test-password",
            role="superadmin",
        )
        folder = Folder.objects.create(name="Documents", created_by=superadmin)
        pdf = PDFFile.objects.create(
            title="Stored artifacts",
            file="pdfs/stored-artifacts.pdf",
            folder=folder,
            uploaded_by=superadmin,
            indexed=False,
            page_chunks=["Searchable stored text"],
            chunk_embeddings=[[1.0, 0.0]],
        )
        self.client.force_login(superadmin)

        response = self.client.post(
            reverse("folder_operations", args=[folder.pk]),
            {"operation": "repair_stored_index"},
            follow=True,
        )
        job = MaintenanceJob.objects.get(kind="repair_indexes")
        with patch("core.maintenance.build_or_load_faiss_index_for_folder", return_value=(object(), [], [])):
            run_job(job)

        self.assertRedirects(response, reverse("dashboard_folder", args=[folder.pk]))
        pdf.refresh_from_db()
        self.assertTrue(pdf.indexed)
        self.assertContains(response, "Repair job queued")

    def test_superadmin_can_reprocess_needed_documents(self):
        superadmin = get_user_model().objects.create_user(
            username="reprocess-superadmin",
            password="test-password",
            role="superadmin",
        )
        folder = Folder.objects.create(name="Documents", created_by=superadmin)
        needed_pdf = PDFFile.objects.create(
            title="Needs index",
            file="pdfs/needs-index.pdf",
            folder=folder,
            uploaded_by=superadmin,
            indexed=False,
        )
        indexed_pdf = PDFFile.objects.create(
            title="Already indexed",
            file="pdfs/already-indexed.pdf",
            folder=folder,
            uploaded_by=superadmin,
            indexed=True,
        )
        self.client.force_login(superadmin)

        response = self.client.post(
            reverse("folder_operations", args=[folder.pk]),
            {"operation": "reprocess_needed"},
            follow=True,
        )
        job = MaintenanceJob.objects.get(kind="reindex_needed")
        with patch("core.maintenance.precompute_pdf_embeddings") as precompute:
            run_job(job)

        self.assertRedirects(response, reverse("dashboard_folder", args=[folder.pk]))
        precompute.assert_called_once_with(needed_pdf)
        self.assertNotEqual(precompute.call_args.args[0].pk, indexed_pdf.pk)
        self.assertContains(response, "Reindex job queued")

    def test_reprocess_needed_preserves_ocr_artifacts_when_pdf_has_no_text(self):
        superadmin = get_user_model().objects.create_user(
            username="ocr-superadmin",
            password="test-password",
            role="superadmin",
        )
        folder = Folder.objects.create(name="Scanned documents", created_by=superadmin)
        pdf = PDFFile.objects.create(
            title="Photo OCR scan",
            file="pdfs/photo-ocr-scan.pdf",
            folder=folder,
            uploaded_by=superadmin,
            indexed=False,
            page_chunks=["OCR transcript already stored"],
            chunk_embeddings=[[1.0, 0.0]],
        )
        self.client.force_login(superadmin)

        response = self.client.post(
            reverse("folder_operations", args=[folder.pk]),
            {"operation": "reprocess_needed"},
            follow=True,
        )
        job = MaintenanceJob.objects.get(kind="reindex_needed")
        with patch(
            "core.maintenance.precompute_pdf_embeddings",
            side_effect=SearchDataIntegrityError("PDF has no extractable text"),
        ), patch(
            "core.maintenance.build_or_load_faiss_index_for_folder",
            return_value=(object(), [], []),
        ) as rebuild:
            run_job(job)

        self.assertRedirects(response, reverse("dashboard_folder", args=[folder.pk]))
        rebuild.assert_called_once_with(folder, force_rebuild=True)
        pdf.refresh_from_db()
        self.assertTrue(pdf.indexed)
        self.assertContains(response, "Reindex job queued")

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


class AuditAndValidationTests(TestCase):
    def setUp(self):
        self.superadmin = get_user_model().objects.create_user(
            username="audit-superadmin",
            password="test-password",
            role="superadmin",
        )

    def test_audit_event_created_on_queue_job(self):
        job = queue_job(kind="validate", requested_by=self.superadmin, scope={"test": True})
        events = MaintenanceAuditEvent.objects.filter(job=job)
        self.assertEqual(events.count(), 1)
        self.assertEqual(events.first().event_type, "queued")
        self.assertEqual(events.first().actor, self.superadmin)

    def test_audit_events_fire_on_job_completion(self):
        folder = Folder.objects.create(name="Audit test", created_by=self.superadmin)
        job = queue_job(kind="validate", requested_by=self.superadmin, folders=[folder])
        with patch("core.maintenance._validate_pdf"):
            run_job(job)
        events = list(MaintenanceAuditEvent.objects.filter(job=job).order_by("created_at").values_list("event_type", flat=True))
        self.assertIn("queued", events)
        self.assertIn("item_completed", events)
        self.assertIn("completed", events)

    def test_audit_events_fire_on_item_failure(self):
        folder = Folder.objects.create(name="Audit fail test", created_by=self.superadmin)
        pdf = PDFFile.objects.create(
            title="Audit fail PDF",
            file="pdfs/audit-fail.pdf",
            folder=folder,
            uploaded_by=self.superadmin,
        )
        job = queue_job(kind="validate", requested_by=self.superadmin, pdfs=[pdf])
        with patch("core.maintenance._validate_pdf", side_effect=SearchDataIntegrityError("missing file")):
            run_job(job)
        events = list(MaintenanceAuditEvent.objects.filter(job=job).values_list("event_type", flat=True))
        self.assertIn("item_failed", events)
        self.assertIn("failed", events)

    def test_record_validation_creates_artifact_validation(self):
        gen = ArtifactGeneration.objects.create(
            generation_id="gen-val-test-001",
            status="validated",
            created_by=self.superadmin,
        )
        _record_validation(gen, validation_type="manifest", status="passed", details={"files": 5}, validated_by=self.superadmin)
        val = ArtifactValidation.objects.get(generation=gen)
        self.assertEqual(val.validation_type, "manifest")
        self.assertEqual(val.status, "passed")
        self.assertEqual(val.details, {"files": 5})

    def test_job_audit_trail_endpoint_returns_json(self):
        job = queue_job(kind="validate", requested_by=self.superadmin)
        self.client.force_login(self.superadmin)
        response = self.client.get(reverse("job_audit_trail", args=[job.public_id]))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["job_id"], str(job.public_id))
        self.assertEqual(len(data["events"]), 1)
        self.assertEqual(data["events"][0]["event_type"], "queued")

    def test_job_audit_trail_requires_superadmin(self):
        admin = get_user_model().objects.create_user(
            username="audit-admin", password="test-password", role="admin"
        )
        job = queue_job(kind="validate", requested_by=self.superadmin)
        self.client.force_login(admin)
        response = self.client.get(reverse("job_audit_trail", args=[job.public_id]))
        self.assertEqual(response.status_code, 403)

    def test_generation_validations_endpoint_returns_json(self):
        gen = ArtifactGeneration.objects.create(
            generation_id="gen-val-endpoint-001",
            status="validated",
            created_by=self.superadmin,
        )
        _record_validation(gen, validation_type="sha256", status="passed", details={"files_verified": 3})
        self.client.force_login(self.superadmin)
        response = self.client.get(reverse("generation_validations", args=["gen-val-endpoint-001"]))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["generation_id"], "gen-val-endpoint-001")
        self.assertEqual(len(data["validations"]), 1)
        self.assertEqual(data["validations"][0]["validation_type"], "sha256")

    def test_generation_validations_404_for_nonexistent(self):
        self.client.force_login(self.superadmin)
        response = self.client.get(reverse("generation_validations", args=["gen-does-not-exist"]))
        self.assertEqual(response.status_code, 404)


class GenerationLifecycleTests(TestCase):
    def setUp(self):
        self.superadmin = get_user_model().objects.create_user(
            username="generation-superadmin",
            password="test-password",
            role="superadmin",
        )

    def _make_generation(self, gen_id="gen-test-001", status="validated"):
        return ArtifactGeneration.objects.create(
            generation_id=gen_id,
            status=status,
            source="local",
            created_by=self.superadmin,
        )

    def test_promote_validated_generation_supersedes_prior_active(self):
        prior = self._make_generation(gen_id="gen-prior-active", status="active")
        new = self._make_generation(gen_id="gen-new-validated", status="validated")

        promote_active_generation("gen-new-validated", requested_by=self.superadmin)

        prior.refresh_from_db()
        new.refresh_from_db()
        self.assertEqual(new.status, "active")
        self.assertIsNotNone(new.promoted_at)
        self.assertEqual(prior.status, "superseded")
        self.assertEqual(prior.superseded_by, new)

    def test_promote_non_validated_generation_raises(self):
        self._make_generation(gen_id="gen-staged-only", status="staged")
        with self.assertRaises(SearchDataIntegrityError):
            promote_active_generation("gen-staged-only", requested_by=self.superadmin)

    def test_promote_nonexistent_generation_raises(self):
        with self.assertRaises(SearchDataIntegrityError):
            promote_active_generation("gen-does-not-exist", requested_by=self.superadmin)

    def test_rollback_to_superseded_generation_promotes_it(self):
        old_active = self._make_generation(gen_id="gen-old-active", status="active")
        new = self._make_generation(gen_id="gen-new-validated", status="validated")
        promote_active_generation("gen-new-validated", requested_by=self.superadmin)

        old_active.refresh_from_db()
        self.assertEqual(old_active.status, "superseded")

        rollback_to_generation("gen-old-active", requested_by=self.superadmin)

        old_active.refresh_from_db()
        new.refresh_from_db()
        self.assertEqual(old_active.status, "active")
        self.assertEqual(new.status, "superseded")
        self.assertEqual(new.superseded_by, old_active)

    def test_purge_non_active_generation(self):
        gen = self._make_generation(gen_id="gen-to-purge", status="superseded")
        purge_generation("gen-to-purge", requested_by=self.superadmin)
        gen.refresh_from_db()
        self.assertEqual(gen.status, "purged")

    def test_purge_active_generation_raises(self):
        self._make_generation(gen_id="gen-active-no-purge", status="active")
        with self.assertRaises(SearchDataIntegrityError):
            purge_generation("gen-active-no-purge", requested_by=self.superadmin)

    def test_purge_expired_generations_skips_active_and_predecessor(self):
        from datetime import timedelta
        from django.utils import timezone
        active = self._make_generation(gen_id="gen-active-exp", status="active")
        predecessor = self._make_generation(gen_id="gen-pred-exp", status="superseded")
        predecessor.superseded_by = active
        predecessor.save()
        expired_other = self._make_generation(gen_id="gen-expired-other", status="superseded")
        expired_other.expires_at = timezone.now() - timedelta(days=1)
        expired_other.save()

        purged = purge_expired_generations(requested_by=self.superadmin)

        self.assertIn("gen-expired-other", purged)
        self.assertNotIn("gen-active-exp", purged)
        self.assertNotIn("gen-pred-exp", purged)
        expired_other.refresh_from_db()
        self.assertEqual(expired_other.status, "purged")

    def test_promote_endpoint_requires_superadmin(self):
        admin = get_user_model().objects.create_user(
            username="gen-admin", password="test-password", role="admin"
        )
        self.client.force_login(admin)
        response = self.client.post(reverse("promote_generation", args=["gen-x"]))
        self.assertEqual(response.status_code, 403)

    def test_rollback_endpoint_redirects_on_success(self):
        self._make_generation(gen_id="gen-rollback-test", status="validated")
        self.client.force_login(self.superadmin)
        response = self.client.post(reverse("rollback_generation", args=["gen-rollback-test"]))
        self.assertRedirects(response, reverse("dashboard"))

    def test_purge_endpoint_redirects_on_success(self):
        gen = self._make_generation(gen_id="gen-purge-test", status="superseded")
        self.client.force_login(self.superadmin)
        response = self.client.post(reverse("purge_generation", args=["gen-purge-test"]))
        self.assertRedirects(response, reverse("dashboard"))
        gen.refresh_from_db()
        self.assertEqual(gen.status, "purged")

    def test_dashboard_renders_generation_lifecycle_table(self):
        self._make_generation(gen_id="gen-visible-001", status="validated")
        self._make_generation(gen_id="gen-visible-002", status="active")
        self.client.force_login(self.superadmin)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Generation Lifecycle")
        self.assertContains(response, "gen-visible-001")
        self.assertContains(response, "gen-visible-002")
        self.assertContains(response, "Promote")
        self.assertContains(response, "Purge Expired")
