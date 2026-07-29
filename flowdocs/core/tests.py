import json
import os
import sqlite3
import tempfile
import time
from decimal import Decimal
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
from .models import Folder, PDFFile, MaintenanceJob, MaintenanceAuditEvent, MaintenancePlan, ArtifactGeneration, ArtifactValidation, SiteSetting
from .maintenance import run_job, queue_job, _audit, _record_validation, promote_active_generation, rollback_to_generation, purge_generation, purge_expired_generations, deprecate_pdf, archive_pdf, restore_pdf
from .maintenance_plans import queue_plan
from .management.commands.run_maintenance_jobs import _recover_orphaned_jobs, _write_heartbeat
from .worker_readiness import heartbeat_path
from .views import _parse_bulk_filters
from .runtime_data_gate import RuntimeDataGateError, seed_pdf_media_report, validate_seed_pdf_media
from .runtime_config import validate_redis_url
from .utils import SearchDataIntegrityError, search_chunks_with_faiss_or_numpy
from .environment import EnvironmentIdentity, AppEnv, BackupRole, DataMode, ExternalSideEffectsMode, EnvironmentIdentityError
from .side_effects import SideEffectPolicy, resolve_email_backend
from .compatibility import check_generation_compatibility
from .artifact_vault import ArtifactVault, VaultConfig


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
            "I am Sahakar AI. Learn how to ask better questions and get more useful answers.",
        )
        self.assertEqual(response.context["welcome_help_label"], "Click Here")
        self.assertEqual(response.context["welcome_prompt_label"], "Try asking")
        self.assertEqual(len(response.context["welcome_prompts"]), 6)
        self.assertEqual(len(response.context["search_loading_stages"]), 3)
        self.assertEqual(response.context["search_messages"]["unexpected"], "Something went wrong")
        self.assertContains(response, "Registrar Co-operative Societies, Maharashtra")
        self.assertContains(response, 'name="language" value="mr"')
        self.assertContains(response, '<html lang="en">')

    def test_public_search_uses_fullscreen_desk_structure(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="workbench"')
        self.assertContains(response, 'id="searchComposer"')
        self.assertContains(response, 'id="aboutDialog"')
        self.assertContains(response, 'data-about-open')
        self.assertContains(response, 'id="source-documents-label"')

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
        self.assertEqual(response.context["search_messages"]["unexpected"], "काहीतरी चूक झाली")
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

    def test_positive_decimal_reader_accepts_fractional_megabytes(self):
        with patch.dict(os.environ, {"MAX_FILE_SIZE_MB": "10.5"}):
            self.assertEqual(
                project_settings._env_positive_decimal("MAX_FILE_SIZE_MB", 10),
                Decimal("10.5"),
            )

    def test_positive_decimal_reader_rejects_zero(self):
        with patch.dict(os.environ, {"MAX_FILE_SIZE_MB": "0"}):
            with self.assertRaisesMessage(
                ImproperlyConfigured,
                "MAX_FILE_SIZE_MB must be a positive number",
            ):
                project_settings._env_positive_decimal("MAX_FILE_SIZE_MB", 10)


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

    def test_upload_size_error_uses_megabytes(self):
        uploaded = SimpleUploadedFile(
            'large.pdf', b'%PDF-' + b'x' * (11 * 1024 * 1024),
            content_type='application/pdf',
        )
        with self.settings(MAX_FILE_SIZE=10 * 1024 * 1024, MAX_FILE_SIZE_MB=10):
            form = UploadForm(data={'title': 'Large'}, files={'file': uploaded})
        self.assertFalse(form.is_valid())
        self.assertIn('10 MB', str(form.errors))


class ArtifactVaultHealthTests(SimpleTestCase):
    def _vault(self, client):
        config = VaultConfig(
            enabled=True,
            endpoint="https://vault.example",
            bucket="artifacts",
            region="us-east-1",
            access_key="access",
            secret_key="secret",
        )
        return ArtifactVault(config=config, client=client)

    def test_health_check_reports_success_only_after_bucket_probe(self):
        class Client:
            def head_bucket(self, **kwargs):
                return {}

        health = self._vault(Client()).health_check()
        self.assertTrue(health.reachable)
        self.assertTrue(health.bucket_exists)
        self.assertTrue(health.healthy)

    def test_health_check_distinguishes_forbidden_from_missing_bucket(self):
        class Forbidden(Exception):
            def __init__(self):
                self.response = {"Error": {"Code": "AccessDenied"}}

        class Missing(Exception):
            def __init__(self):
                self.response = {"Error": {"Code": "NoSuchBucket"}}

        class Client:
            def __init__(self, error):
                self.error = error

            def head_bucket(self, **kwargs):
                raise self.error()

        forbidden = self._vault(Client(Forbidden)).health_check()
        missing = self._vault(Client(Missing)).health_check()
        self.assertTrue(forbidden.reachable)
        self.assertIsNone(forbidden.bucket_exists)
        self.assertFalse(forbidden.healthy)
        self.assertTrue(missing.reachable)
        self.assertFalse(missing.bucket_exists)
        self.assertFalse(missing.healthy)


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

    def test_admin_registration_has_shared_breadcrumb(self):
        admin = get_user_model().objects.create_user(
            username="breadcrumb-admin",
            password="test-password",
            role="admin",
        )
        self.client.force_login(admin)
        response = self.client.get(reverse("register"))
        self.assertContains(response, 'aria-label="breadcrumb"')
        self.assertContains(response, 'href="/dashboard/"')
        self.assertContains(response, "Create user")

    def test_legacy_vault_page_redirects_to_workbench(self):
        superadmin = get_user_model().objects.create_user(
            username="breadcrumb-superadmin",
            password="test-password",
            role="superadmin",
        )
        self.client.force_login(superadmin)
        response = self.client.get(reverse("vault_operations"))
        self.assertRedirects(
            response,
            f"{reverse('operations_panel')}?section=maintenance",
            fetch_redirect_response=False,
        )

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

    def test_public_search_uses_hallmark_accessible_shell(self):
        response = self.client.get(reverse("home"))
        self.assertContains(response, 'class="workbench-header"')
        self.assertContains(response, 'class="workbench-brand__department"')
        self.assertContains(response, 'aria-label="Public service links"')
        self.assertContains(response, 'id="search-page-heading"')
        self.assertContains(response, 'aria-label="Search answers"')
        self.assertContains(response, "Contact on WhatsApp")
        self.assertContains(response, "Send feedback")
        self.assertContains(response, "civic-workbench.css")
        self.assertNotContains(response, "search-theme-b")
        self.assertNotContains(response, "search-theme-c")
        self.assertNotContains(response, "public-search-theme-picker")
        self.assertNotContains(response, "⚠️")

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
        response = self.client.get(reverse("search_query"), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "search.js")
        self.assertContains(response, 'id="userQuery"')
        self.assertContains(response, "Answers are informational")
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
    databases = {"default", "control"}

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
        self.assertContains(response, "Operations Cockpit")
        self.assertContains(response, "Needs attention")
        self.assertContains(response, "Category Yard")
        self.assertContains(response, "Search readiness is unavailable")
        self.assertContains(response, "Verified runtime authority is required")
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
        self.assertContains(response, "शोध तयारीची स्थिती उपलब्ध नाही")
        self.assertContains(
            response, "पडताळलेली अधिकृत कार्यरत स्थिती आवश्यक आहे"
        )

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
        self.assertNotContains(response, "Folder 24")
        self.assertContains(response, "Page 1 of 2")
        page_two = self.client.get(reverse("dashboard"), {"page": 2})
        self.assertContains(page_two, "Folder 24")
        self.assertContains(response, "Category Yard")
        self.assertContains(response, "Search readiness")
        self.assertContains(response, 'aria-label="Close"', count=25)
        self.assertContains(response, "Delete this category and all PDFs inside it")

    def test_dashboard_filters_categories_server_side(self):
        self.client.force_login(self.user)
        Folder.objects.create(name="Audit records", created_by=self.user)
        Folder.objects.create(name="Housing records", created_by=self.user)

        response = self.client.get(reverse("dashboard"), {"category_q": "audit"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Audit records")
        self.assertNotContains(response, "Housing records")
        self.assertContains(response, "1 category matches the current scope")
        self.assertContains(response, 'value="audit"')

    def test_dashboard_prioritizes_actionable_conditions(self):
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
        self.assertContains(response, "Search readiness is unavailable")
        self.assertContains(response, "Index debt cannot be assessed safely")
        self.assertNotContains(response, "Documents need index review")
        self.assertNotContains(response, "1/2")
        self.assertNotContains(response, "index_debt_present")
        self.assertContains(response, "Needs index lane")
        self.assertContains(response, "Index review")
        self.assertContains(response, "Owner review lane")
        self.assertContains(response, "Document provenance needs review")
        self.assertNotContains(response, "provenance_review_required")
        self.assertContains(response, "Empty lane")
        self.assertContains(response, "Categories are awaiting intake")
        self.assertContains(response, reverse("dashboard_folder", args=[index_folder.pk]))
        self.assertContains(response, reverse("dashboard_folder", args=[owner_folder.pk]))
        self.assertContains(response, reverse("dashboard_folder", args=[empty_folder.pk]))
        self.assertNotContains(response, "Prefer rename or quarantine over delete")

    def test_dashboard_filters_readiness_provenance_and_occupancy(self):
        self.client.force_login(self.user)
        ready = Folder.objects.create(name="Ready lane", created_by=self.user)
        PDFFile.objects.create(
            title="Ready",
            file="pdfs/ready.pdf",
            folder=ready,
            uploaded_by=self.user,
            indexed=True,
        )
        debt = Folder.objects.create(name="Debt lane", created_by=self.user)
        PDFFile.objects.create(
            title="Debt",
            file="pdfs/debt.pdf",
            folder=debt,
            uploaded_by=None,
            indexed=False,
        )
        Folder.objects.create(name="Empty lane", created_by=self.user)

        response = self.client.get(
            reverse("dashboard"),
            {"readiness": "needs_index", "provenance": "unknown"},
        )
        category_names = [
            folder.name
            for folder in response.context["cockpit"]["category_page"].object_list
        ]
        self.assertEqual(category_names, ["Debt lane"])
        self.assertContains(response, 'option value="needs_index" selected')
        self.assertContains(response, 'option value="unknown" selected')

        empty = self.client.get(reverse("dashboard"), {"occupancy": "empty"})
        empty_names = [
            folder.name
            for folder in empty.context["cockpit"]["category_page"].object_list
        ]
        self.assertEqual(empty_names, ["Empty lane"])

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
        self.assertContains(response, 'aria-label="Close"', count=2)
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

    def test_legacy_job_action_cannot_cancel_or_retry_maintenance_job(self):
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

        self.assertEqual(response.status_code, 410)
        self.assertEqual(response.json()["error"], "operation_replaced")
        queued.refresh_from_db()
        self.assertEqual(queued.status, "queued")

        failed = MaintenanceJob.objects.create(
            kind="validate", status="failed", failed_items=1, error_summary="old error"
        )
        response = self.client.post(
            reverse("maintenance_job_action", args=[failed.public_id]),
            {"action": "retry"},
        )

        self.assertEqual(response.status_code, 410)
        self.assertEqual(response.json()["error"], "operation_replaced")
        failed.refresh_from_db()
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.failed_items, 1)
        self.assertEqual(failed.error_summary, "old error")

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

        with override_settings(
            LOCAL_INDEX_MAINTENANCE_ENABLED=True,
            VAULT_MUTATION_TRACKING_ENABLED=True,
            MAINTENANCE_WORKER_READINESS_REQUIRED=False,
            ACTIVE_RUNTIME=None,
        ):
            response = self.client.post(
                reverse("folder_operations", args=[folder.pk]),
                {"operation": "repair_stored_index"},
            )
            plan = MaintenancePlan.objects.get(operation="repair_indexes")
            with patch(
                "core.maintenance_plans.create_set",
                return_value={"set_id": "rs-test"},
            ):
                job = queue_plan(plan=plan, actor=superadmin)
        with patch("core.maintenance.build_or_load_faiss_index_for_folder", return_value=(object(), [], [])):
            run_job(job)

        self.assertRedirects(
            response,
            f"{reverse('operations_panel')}?section=maintenance&plan={plan.public_id}",
            fetch_redirect_response=False,
        )
        pdf.refresh_from_db()
        self.assertTrue(pdf.indexed)

    def test_superadmin_can_reprocess_needed_documents(self):
        superadmin = get_user_model().objects.create_user(
            username="reprocess-superadmin",
            password="test-password",
            role="superadmin",
        )
        folder = Folder.objects.create(name="Documents", created_by=superadmin)
        needed_pdf = PDFFile.objects.create(
            title="Needs index",
            file=SimpleUploadedFile("needs-index.pdf", b"%PDF-1.4"),
            folder=folder,
            uploaded_by=superadmin,
            indexed=False,
        )
        indexed_pdf = PDFFile.objects.create(
            title="Already indexed",
            file=SimpleUploadedFile("already-indexed.pdf", b"%PDF-1.4"),
            folder=folder,
            uploaded_by=superadmin,
            indexed=True,
            lifecycle="ready",
            page_chunks=["Already searchable"],
            chunk_embeddings=[[1.0, 0.0]],
        )
        self.client.force_login(superadmin)

        with override_settings(
            LOCAL_INDEX_MAINTENANCE_ENABLED=True,
            VAULT_MUTATION_TRACKING_ENABLED=True,
            MAINTENANCE_WORKER_READINESS_REQUIRED=False,
            EXTERNAL_EMBEDDINGS_ENABLED=True,
            ACTIVE_RUNTIME=None,
        ):
            response = self.client.post(
                reverse("folder_operations", args=[folder.pk]),
                {"operation": "reprocess_needed"},
            )
            plan = MaintenancePlan.objects.get(operation="reindex_needed")
            with patch(
                "core.maintenance_plans.create_set",
                return_value={"set_id": "rs-test"},
            ):
                job = queue_plan(plan=plan, actor=superadmin)
        with patch("core.maintenance.precompute_pdf_embeddings") as precompute:
            with patch("core.maintenance._repair_folder"):
                run_job(job)

        self.assertRedirects(
            response,
            f"{reverse('operations_panel')}?section=maintenance&plan={plan.public_id}",
            fetch_redirect_response=False,
        )
        precompute.assert_called_once_with(needed_pdf, rebuild_index=False)
        self.assertNotEqual(precompute.call_args.args[0].pk, indexed_pdf.pk)

    def test_repair_stored_index_preserves_ocr_artifacts_without_embedding(self):
        superadmin = get_user_model().objects.create_user(
            username="ocr-superadmin",
            password="test-password",
            role="superadmin",
        )
        folder = Folder.objects.create(name="Scanned documents", created_by=superadmin)
        pdf = PDFFile.objects.create(
            title="Photo OCR scan",
            file=SimpleUploadedFile("photo-ocr-scan.pdf", b"%PDF-1.4"),
            folder=folder,
            uploaded_by=superadmin,
            indexed=False,
            page_chunks=["OCR transcript already stored"],
            chunk_embeddings=[[1.0, 0.0]],
        )
        self.client.force_login(superadmin)

        with override_settings(
            LOCAL_INDEX_MAINTENANCE_ENABLED=True,
            VAULT_MUTATION_TRACKING_ENABLED=True,
            MAINTENANCE_WORKER_READINESS_REQUIRED=False,
            EXTERNAL_EMBEDDINGS_ENABLED=True,
            ACTIVE_RUNTIME=None,
        ):
            response = self.client.post(
                reverse("folder_operations", args=[folder.pk]),
                {"operation": "repair_stored_index"},
            )
            plan = MaintenancePlan.objects.get(operation="repair_indexes")
            with patch(
                "core.maintenance_plans.create_set",
                return_value={"set_id": "rs-test"},
            ):
                job = queue_plan(plan=plan, actor=superadmin)
        with patch(
            "core.maintenance.precompute_pdf_embeddings"
        ) as precompute, patch(
            "core.maintenance.build_or_load_faiss_index_for_folder",
            return_value=(object(), [], []),
        ) as rebuild:
            run_job(job)

        self.assertRedirects(
            response,
            f"{reverse('operations_panel')}?section=maintenance&plan={plan.public_id}",
            fetch_redirect_response=False,
        )
        precompute.assert_not_called()
        rebuild.assert_called_once_with(folder, force_rebuild=True)
        pdf.refresh_from_db()
        self.assertTrue(pdf.indexed)

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
    databases = {"default", "control"}

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

    def test_legacy_rollback_endpoint_does_not_relabel_generation(self):
        generation = self._make_generation(
            gen_id="gen-rollback-test", status="validated"
        )
        self.client.force_login(self.superadmin)
        response = self.client.post(reverse("rollback_generation", args=["gen-rollback-test"]))
        self.assertRedirects(
            response,
            f"{reverse('operations_panel')}?section=generations",
            fetch_redirect_response=False,
        )
        generation.refresh_from_db()
        self.assertEqual(generation.status, "validated")

    def test_legacy_purge_endpoint_does_not_claim_deletion(self):
        gen = self._make_generation(gen_id="gen-purge-test", status="superseded")
        self.client.force_login(self.superadmin)
        response = self.client.post(reverse("purge_generation", args=["gen-purge-test"]))
        self.assertRedirects(
            response,
            f"{reverse('operations_panel')}?section=generations",
            fetch_redirect_response=False,
        )
        gen.refresh_from_db()
        self.assertEqual(gen.status, "superseded")

    def test_dashboard_renders_generation_lifecycle_table(self):
        self._make_generation(gen_id="gen-visible-001", status="validated")
        self._make_generation(gen_id="gen-visible-002", status="active")
        self.client.force_login(self.superadmin)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Data Generations")
        self.assertContains(
            response,
            f"{reverse('operations_panel')}?section=generations",
        )

        # The retired mixed-control page now routes to Documents & Indexes.
        vault_response = self.client.get(reverse("vault_operations"))
        self.assertRedirects(
            vault_response,
            f"{reverse('operations_panel')}?section=maintenance",
            fetch_redirect_response=False,
        )
class BulkFilterTests(TestCase):
    databases = {"default", "control"}

    def setUp(self):
        self.superadmin = get_user_model().objects.create_user(
            username="filter-superadmin",
            password="test-password",
            role="superadmin",
        )
        self.folder = Folder.objects.create(name="Filter test", created_by=self.superadmin)
        self.pdf_indexed = PDFFile.objects.create(
            title="Indexed doc",
            file="pdfs/indexed.pdf",
            folder=self.folder,
            uploaded_by=self.superadmin,
            indexed=True,
            category="acts",
            subject="housing",
            keywords=["cooperative", "society"],
        )
        self.pdf_not_indexed = PDFFile.objects.create(
            title="Not indexed doc",
            file="pdfs/not-indexed.pdf",
            folder=self.folder,
            uploaded_by=self.superadmin,
            indexed=False,
            category="rules",
            subject="audit",
        )

    def _fake_request(self, **params):
        return SimpleNamespace(POST=params, GET={})

    def test_filter_by_category(self):
        q, filters = _parse_bulk_filters(self._fake_request(filter_category="acts"))
        pdfs = PDFFile.objects.filter(q)
        self.assertEqual(pdfs.count(), 1)
        self.assertEqual(pdfs.first().title, "Indexed doc")

    def test_filter_by_indexed_status(self):
        q, _ = _parse_bulk_filters(self._fake_request(filter_indexed="false"))
        pdfs = PDFFile.objects.filter(q)
        self.assertEqual(pdfs.count(), 1)
        self.assertEqual(pdfs.first().title, "Not indexed doc")

    def test_filter_by_keywords(self):
        q, _ = _parse_bulk_filters(self._fake_request(filter_keywords="cooperative"))
        pdfs = PDFFile.objects.filter(q)
        self.assertEqual(pdfs.count(), 1)
        self.assertEqual(pdfs.first().title, "Indexed doc")

    def test_filter_by_subject(self):
        q, _ = _parse_bulk_filters(self._fake_request(filter_subject="audit"))
        pdfs = PDFFile.objects.filter(q)
        self.assertEqual(pdfs.count(), 1)
        self.assertEqual(pdfs.first().title, "Not indexed doc")

    def test_filter_combined(self):
        q, _ = _parse_bulk_filters(self._fake_request(filter_category="acts", filter_indexed="true"))
        pdfs = PDFFile.objects.filter(q)
        self.assertEqual(pdfs.count(), 1)
        self.assertEqual(pdfs.first().title, "Indexed doc")

    def test_filter_no_params_returns_all(self):
        q, filters = _parse_bulk_filters(self._fake_request())
        self.assertEqual(filters, {})
        self.assertEqual(PDFFile.objects.filter(q).count(), 2)

    def test_legacy_bulk_filter_preview_is_rejected(self):
        self.client.force_login(self.superadmin)
        response = self.client.get(
            reverse("bulk_filter_preview"),
            {"folder_ids": str(self.folder.pk), "filter_indexed": "true"},
        )
        self.assertEqual(response.status_code, 410)
        data = response.json()
        self.assertEqual(data["error"], "operation_replaced")
        self.assertIn("section=maintenance", data["workbench_url"])

    def test_bulk_filter_preview_requires_superadmin(self):
        admin = get_user_model().objects.create_user(
            username="filter-admin", password="test-password", role="admin"
        )
        self.client.force_login(admin)
        response = self.client.get(reverse("bulk_filter_preview"))
        self.assertEqual(response.status_code, 403)

    def test_dashboard_renders_filter_panel(self):
        self.client.force_login(self.superadmin)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bulk Operations")
        self.assertContains(response, 'name="readiness"')
        self.assertContains(response, 'name="provenance"')
        self.assertContains(response, 'name="occupancy"')
        self.assertContains(response, 'name="ordering"')
        self.assertContains(
            response,
            f"{reverse('operations_panel')}?section=maintenance",
        )


class JobDrawerTests(TestCase):
    databases = {"default", "control"}

    def setUp(self):
        self.superadmin = get_user_model().objects.create_user(
            username="drawer-superadmin",
            password="test-password",
            role="superadmin",
        )

    def test_job_status_endpoint_returns_json(self):
        job = MaintenanceJob.objects.create(
            kind="validate",
            status="running",
            total_items=10,
            completed_items=5,
            requested_by=self.superadmin,
        )
        self.client.force_login(self.superadmin)
        response = self.client.get(reverse("job_status", args=[job.public_id]))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["job_id"], str(job.public_id))
        self.assertEqual(data["status"], "running")
        self.assertEqual(data["total_items"], 10)
        self.assertEqual(data["completed_items"], 5)
        self.assertEqual(data["progress"], 50)

    def test_job_status_requires_superadmin(self):
        admin = get_user_model().objects.create_user(
            username="drawer-admin", password="test-password", role="admin"
        )
        job = MaintenanceJob.objects.create(kind="validate", status="queued")
        self.client.force_login(admin)
        response = self.client.get(reverse("job_status", args=[job.public_id]))
        self.assertEqual(response.status_code, 403)

    def test_active_jobs_endpoint_returns_only_active(self):
        running = MaintenanceJob.objects.create(kind="validate", status="running")
        queued = MaintenanceJob.objects.create(
            kind="reindex_selected", status="queued"
        )
        MaintenanceJob.objects.create(kind="validate", status="completed")
        MaintenanceJob.objects.create(kind="validate", status="failed")
        self.client.force_login(self.superadmin)
        response = self.client.get(reverse("active_jobs"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        job_ids = [j["job_id"] for j in data["jobs"]]
        self.assertEqual(data["count"], 2)
        self.assertIn(str(running.public_id), job_ids)
        self.assertIn(str(queued.public_id), job_ids)

    def test_active_jobs_excludes_legacy_generation_kinds(self):
        queued = MaintenanceJob.objects.create(
            kind="reindex_selected",
            status="queued",
        )
        MaintenanceJob.objects.create(
            kind="sync_generation",
            status="running",
        )
        self.client.force_login(self.superadmin)
        response = self.client.get(reverse("active_jobs"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        job_ids = [j["job_id"] for j in data["jobs"]]
        self.assertIn(str(queued.public_id), job_ids)
        self.assertNotIn("sync_generation", [j["kind"] for j in data["jobs"]])

    def test_active_jobs_requires_superadmin(self):
        admin = get_user_model().objects.create_user(
            username="active-admin", password="test-password", role="admin"
        )
        self.client.force_login(admin)
        response = self.client.get(reverse("active_jobs"))
        self.assertEqual(response.status_code, 403)

    def test_dashboard_renders_single_active_work_projection(self):
        MaintenanceJob.objects.create(
            kind="validate",
            status="running",
            total_items=2,
            completed_items=1,
            requested_by=self.superadmin,
        )
        self.client.force_login(self.superadmin)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Active Work")
        self.assertContains(response, "1/2")
        self.assertContains(response, "?section=maintenance&job=")
        self.assertNotContains(response, "job-drawer-toggle")
        self.assertNotContains(response, "/dashboard/maintenance/")

    def test_dashboard_active_work_excludes_terminal_jobs(self):
        active = MaintenanceJob.objects.create(
            kind="validate",
            status="running",
            total_items=2,
            completed_items=1,
            requested_by=self.superadmin,
        )
        for status in ("completed", "failed", "cancelled"):
            MaintenanceJob.objects.create(
                kind="validate",
                status=status,
                total_items=99,
                completed_items=99,
                requested_by=self.superadmin,
            )
        self.client.force_login(self.superadmin)

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(
            [job.pk for job in response.context["cockpit"]["maintenance_jobs"]],
            [active.pk],
        )
        self.assertContains(response, "1/2")
        self.assertNotContains(response, "99/99")

    def test_completed_jobs_cannot_crowd_active_work_out_of_projection(self):
        active = MaintenanceJob.objects.create(
            kind="validate",
            status="queued",
            requested_by=self.superadmin,
        )
        for _ in range(6):
            MaintenanceJob.objects.create(
                kind="validate",
                status="completed",
                requested_by=self.superadmin,
            )
        self.client.force_login(self.superadmin)

        response = self.client.get(reverse("dashboard"))

        jobs = response.context["cockpit"]["maintenance_jobs"]
        self.assertEqual([job.pk for job in jobs], [active.pk])
        self.assertContains(response, str(active.public_id))

    def test_dashboard_does_not_render_drawer_for_admin(self):
        admin = get_user_model().objects.create_user(
            username="no-drawer-admin", password="test-password", role="admin"
        )
        self.client.force_login(admin)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "job-drawer-toggle")

    def test_legacy_job_action_never_mutates_historical_generation_job(self):
        self.client.force_login(self.superadmin)
        job = MaintenanceJob.objects.create(
            kind="restore_generation", status="running"
        )
        response = self.client.post(
            reverse("maintenance_job_action", args=[job.public_id]),
            {"action": "cancel"},
        )

        self.assertEqual(response.status_code, 410)
        self.assertEqual(response.json()["error"], "operation_replaced")
        job.refresh_from_db()
        self.assertEqual(job.status, "running")


class DocumentLifecycleTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(
            username="lifecycle-admin",
            password="test-password",
            role="admin",
        )
        self.folder = Folder.objects.create(name="Lifecycle test", created_by=self.admin)
        self.pdf = PDFFile.objects.create(
            title="Lifecycle PDF",
            file="pdfs/lifecycle.pdf",
            folder=self.folder,
            uploaded_by=self.admin,
            indexed=True,
        )

    def test_default_lifecycle_is_uploaded(self):
        self.assertEqual(self.pdf.lifecycle, "uploaded")

    def test_deprecate_sets_lifecycle_and_unindexes(self):
        deprecate_pdf(self.pdf, requested_by=self.admin)
        self.pdf.refresh_from_db()
        self.assertEqual(self.pdf.lifecycle, "deprecated")
        self.assertFalse(self.pdf.indexed)

    def test_archive_sets_lifecycle_and_unindexes(self):
        archive_pdf(self.pdf, requested_by=self.admin)
        self.pdf.refresh_from_db()
        self.assertEqual(self.pdf.lifecycle, "archived")
        self.assertFalse(self.pdf.indexed)

    def test_restore_sets_lifecycle_to_uploaded(self):
        archive_pdf(self.pdf, requested_by=self.admin)
        restore_pdf(self.pdf, requested_by=self.admin)
        self.pdf.refresh_from_db()
        self.assertEqual(self.pdf.lifecycle, "uploaded")
        self.assertFalse(self.pdf.indexed)

    def test_deprecate_archived_raises(self):
        archive_pdf(self.pdf, requested_by=self.admin)
        with self.assertRaises(SearchDataIntegrityError):
            deprecate_pdf(self.pdf, requested_by=self.admin)

    def test_deprecated_pdf_excluded_from_public_visible_pdfs(self):
        deprecate_pdf(self.pdf, requested_by=self.admin)
        from .views import visible_pdfs
        visible = visible_pdfs(self.admin, public=True)
        self.assertFalse(visible.filter(pk=self.pdf.pk).exists())

    def test_archived_pdf_excluded_from_public_visible_pdfs(self):
        archive_pdf(self.pdf, requested_by=self.admin)
        from .views import visible_pdfs
        visible = visible_pdfs(self.admin, public=True)
        self.assertFalse(visible.filter(pk=self.pdf.pk).exists())

    def test_deprecate_endpoint_requires_admin(self):
        ordinary = get_user_model().objects.create_user(
            username="lifecycle-ordinary", password="test-password", role="user"
        )
        self.client.force_login(ordinary)
        response = self.client.post(reverse("deprecate_pdf", args=[self.pdf.pk]))
        self.assertEqual(response.status_code, 403)

    def test_deprecate_endpoint_redirects_on_success(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("deprecate_pdf", args=[self.pdf.pk]))
        self.assertEqual(response.status_code, 302)
        self.pdf.refresh_from_db()
        self.assertEqual(self.pdf.lifecycle, "deprecated")

    def test_restore_endpoint_redirects_on_success(self):
        archive_pdf(self.pdf, requested_by=self.admin)
        self.client.force_login(self.admin)
        response = self.client.post(reverse("restore_pdf", args=[self.pdf.pk]))
        self.assertEqual(response.status_code, 302)
        self.pdf.refresh_from_db()
        self.assertEqual(self.pdf.lifecycle, "uploaded")

    def test_dashboard_renders_lifecycle_badges(self):
        deprecate_pdf(self.pdf, requested_by=self.admin)
        self.client.force_login(self.admin)
        response = self.client.get(reverse("dashboard_folder", args=[self.folder.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Deprecated")
        self.assertContains(response, "Restore")

    def test_dashboard_renders_deprecate_and_archive_buttons(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("dashboard_folder", args=[self.folder.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Deprecate")
        self.assertContains(response, "Archive")


class SeoAeoTests(TestCase):
    def test_robots_txt_allows_public_disallows_admin(self):
        response = self.client.get("/robots.txt")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/plain")
        content = response.content.decode()
        self.assertIn("Allow: /", content)
        self.assertIn("Allow: /search/", content)
        self.assertIn("Disallow: /dashboard/", content)
        self.assertIn("Disallow: /register/", content)
        self.assertIn("Sitemap: https://ai-sahakar.net/sitemap.xml", content)

    def test_sitemap_xml_contains_public_urls(self):
        response = self.client.get("/sitemap.xml")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/xml")
        content = response.content.decode()
        self.assertIn("<?xml", content)
        self.assertIn("<urlset", content)
        self.assertIn("https://ai-sahakar.net/", content)
        self.assertNotIn("https://ai-sahakar.net/search/", content)
        self.assertNotIn("https://ai-sahakar.net/livez", content)
        self.assertNotIn("https://ai-sahakar.net/readyz", content)
        self.assertIn("<lastmod>", content)
        self.assertIn("<priority>", content)

    def test_search_page_has_seo_metadata(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sahakar AI")
        self.assertContains(response, 'name="description"')
        self.assertContains(response, 'rel="canonical"')
        self.assertContains(response, 'property="og:title"')
        self.assertContains(response, 'property="og:description"')
        self.assertContains(response, 'name="twitter:card"')
        self.assertContains(response, 'name="robots"')

    def test_search_page_has_json_ld_structured_data(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'type="application/ld+json"')
        self.assertContains(response, '"@type": "WebSite"')
        self.assertContains(response, '"@type": "FAQPage"')
        self.assertContains(response, '"@type": "Organization"')
        self.assertContains(response, '"@type": "WebPage"')

    def test_static_service_description_visible_when_enabled(self):
        with self.settings(DISPLAY_SERVICE_FOOTER=True):
            response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "What is Sahakar AI?")
        self.assertContains(response, "How to ask better questions")
        self.assertContains(response, "Important disclaimer")

    def test_core_service_description_is_visible_by_default(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "What is Sahakar AI?")
        self.assertContains(response, "Common questions")
        self.assertContains(response, "seo-support")

    def test_extended_service_description_can_be_disabled(self):
        with self.settings(DISPLAY_SERVICE_FOOTER=False):
            response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "How to ask better questions")
        self.assertNotContains(response, "Important disclaimer")

    def test_search_page_is_canonicalized_to_homepage(self):
        response = self.client.get(reverse("search_query"))
        self.assertRedirects(response, reverse("home"), status_code=301)

    def test_search_page_has_descriptive_alt_text(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'alt="Banner"')
        self.assertNotContains(response, 'alt="Logo 4"')

    def test_search_page_does_not_claim_unstable_hreflang_alternates(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'hreflang="en"')
        self.assertNotContains(response, 'hreflang="mr"')
        self.assertNotContains(response, 'hreflang="x-default"')

    def test_validate_public_html_script_passes(self):
        response = self.client.get(reverse("home"))
        import importlib.util
        import os
        script_path = os.path.join(settings.BASE_DIR, "..", "scripts", "ci", "validate_public_html.py")
        spec = importlib.util.spec_from_file_location("validate_public_html", script_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        issues = mod.validate(response.content.decode())
        self.assertEqual(issues, [], f"SEO validation failed: {issues}")


class WorkerRecoveryTests(TestCase):
    def setUp(self):
        self.superadmin = get_user_model().objects.create_user(
            username="worker-recovery-admin",
            password="test-password",
            role="superadmin",
        )

    def test_recover_orphaned_jobs_resets_running_to_queued(self):
        from datetime import timedelta
        from django.utils import timezone
        past = timezone.now() - timedelta(minutes=10)
        job = MaintenanceJob.objects.create(
            kind="validate",
            status="running",
            started_at=past,
        )
        recovered = _recover_orphaned_jobs()
        self.assertEqual(recovered, 1)
        job.refresh_from_db()
        self.assertEqual(job.status, "queued")
        self.assertIsNone(job.started_at)

    def test_recover_orphaned_jobs_skips_recently_started(self):
        from django.utils import timezone
        job = MaintenanceJob.objects.create(
            kind="validate",
            status="running",
            started_at=timezone.now(),
        )
        recovered = _recover_orphaned_jobs()
        self.assertEqual(recovered, 0)
        job.refresh_from_db()
        self.assertEqual(job.status, "running")

    def test_recover_orphaned_jobs_creates_audit_event(self):
        from datetime import timedelta
        from django.utils import timezone
        past = timezone.now() - timedelta(minutes=10)
        job = MaintenanceJob.objects.create(
            kind="reindex_all",
            status="running",
            started_at=past,
        )
        _recover_orphaned_jobs()
        events = MaintenanceAuditEvent.objects.filter(job=job, event_type="worker_died")
        self.assertEqual(events.count(), 1)
        self.assertEqual(events.first().payload["previous_status"], "running")

    def test_recover_orphaned_jobs_resets_running_items(self):
        from datetime import timedelta
        from django.utils import timezone
        folder = Folder.objects.create(name="Recovery test", created_by=self.superadmin)
        pdf = PDFFile.objects.create(
            title="Recovery PDF",
            file="pdfs/recovery.pdf",
            folder=folder,
            uploaded_by=self.superadmin,
        )
        past = timezone.now() - timedelta(minutes=10)
        job = queue_job(kind="reindex_needed", requested_by=self.superadmin, pdfs=[pdf])
        job.status = "running"
        job.started_at = past
        job.save()
        job.items.update(status="running")
        _recover_orphaned_jobs()
        self.assertEqual(job.items.filter(status="queued").count(), 1)
        self.assertEqual(job.items.filter(status="running").count(), 0)

    def test_worker_died_is_valid_audit_event_type(self):
        from datetime import timedelta
        from django.utils import timezone
        past = timezone.now() - timedelta(minutes=10)
        job = MaintenanceJob.objects.create(
            kind="validate",
            status="running",
            started_at=past,
        )
        _recover_orphaned_jobs()
        event = MaintenanceAuditEvent.objects.get(job=job, event_type="worker_died")
        self.assertEqual(event.event_type, "worker_died")

    def test_heartbeat_file_contains_timestamp(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(
            MAINTENANCE_WORKER_HEARTBEAT_PATH=Path(tmpdir) / "worker.heartbeat"
        ):
            _write_heartbeat()
            lines = heartbeat_path().read_text(encoding="ascii").strip().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertGreater(float(lines[0]), 0)

    def test_heartbeat_timestamp_is_recent(self):
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(
            MAINTENANCE_WORKER_HEARTBEAT_PATH=Path(tmpdir) / "worker.heartbeat"
        ):
            _write_heartbeat()
            timestamp = float(
                heartbeat_path().read_text(encoding="ascii").strip()
            )
        age = time.time() - timestamp
        self.assertLess(age, 5)


class EnvironmentIdentityTests(TestCase):
    def test_missing_app_env_fails_closed(self):
        env = {"ALLOW_INSECURE_DEFAULTS": "0"}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(EnvironmentIdentityError):
                EnvironmentIdentity.from_env(environ=env)

    def test_prod_requires_dataset_id(self):
        identity = EnvironmentIdentity.from_env(environ={
            "APP_ENV": "production",
            "BACKUP_ROLE": "disabled",
        })
        errors = identity.validate()
        self.assertTrue(any("DATASET_ID" in e for e in errors))

    def test_prod_requires_production_source_id(self):
        identity = EnvironmentIdentity.from_env(environ={
            "APP_ENV": "production",
            "DATASET_ID": "prod-dataset",
            "DEPLOYMENT_ID": "prod-mum-01",
            "BACKUP_ROLE": "disabled",
        })
        errors = identity.validate()
        self.assertTrue(any("PRODUCTION_SOURCE_ID" in e for e in errors))

    def test_valid_production_config(self):
        identity = EnvironmentIdentity.from_env(environ={
            "APP_ENV": "production",
            "DATASET_ID": "prod-dataset",
            "AUTHORITATIVE_DATASET_ID": "prod-dataset",
            "PRODUCTION_SOURCE_ID": "prod-primary",
            "DEPLOYMENT_ID": "prod-mum-01",
            "BACKUP_ROLE": "reader",
        })
        errors = identity.validate()
        self.assertEqual(errors, [])

    def test_writer_requires_image_digest(self):
        identity = EnvironmentIdentity.from_env(environ={
            "APP_ENV": "production",
            "DATASET_ID": "prod-dataset",
            "AUTHORITATIVE_DATASET_ID": "prod-dataset",
            "PRODUCTION_SOURCE_ID": "prod-primary",
            "DEPLOYMENT_ID": "prod-mum-01",
            "BACKUP_ROLE": "writer",
        })
        errors = identity.validate()
        self.assertTrue(any("PRODUCTION_SOURCE_ID" in e or "APP_IMAGE_DIGEST" in e or "ARTIFACT_VAULT" in e for e in errors), errors)

    def test_writer_requires_vault_enabled(self):
        identity = EnvironmentIdentity.from_env(environ={
            "APP_ENV": "production",
            "DATASET_ID": "prod-dataset",
            "AUTHORITATIVE_DATASET_ID": "prod-dataset",
            "PRODUCTION_SOURCE_ID": "prod-primary",
            "DEPLOYMENT_ID": "prod-mum-01",
            "BACKUP_ROLE": "writer",
            "APP_IMAGE_DIGEST": "sha256:abc123",
        })
        with patch.dict(os.environ, {"ARTIFACT_VAULT_ENABLED": "0"}, clear=False):
            errors = identity.validate()
        self.assertTrue(any("ARTIFACT_VAULT_ENABLED" in e for e in errors))

    def test_nonprod_disables_side_effects_by_default(self):
        identity = EnvironmentIdentity.from_env(environ={
            "APP_ENV": "staging",
            "DATA_MODE": "local",
            "RESTORE_SOURCE_DATASET_ID": "prod-dataset",
        })
        self.assertEqual(
            identity.external_side_effects,
            ExternalSideEffectsMode.DISABLED,
        )

    def test_empty_data_mode_rejected_in_production(self):
        identity = EnvironmentIdentity.from_env(environ={
            "APP_ENV": "production",
            "DATASET_ID": "prod-dataset",
            "AUTHORITATIVE_DATASET_ID": "prod-dataset",
            "PRODUCTION_SOURCE_ID": "prod-primary",
            "DEPLOYMENT_ID": "prod-mum-01",
            "DATA_MODE": "empty",
            "BACKUP_ROLE": "disabled",
        })
        errors = identity.validate()
        self.assertTrue(any("DATA_MODE" in e for e in errors))

    def test_exact_production_rejected_in_production(self):
        identity = EnvironmentIdentity.from_env(environ={
            "APP_ENV": "production",
            "DATASET_ID": "prod-dataset",
            "AUTHORITATIVE_DATASET_ID": "prod-dataset",
            "PRODUCTION_SOURCE_ID": "prod-primary",
            "DEPLOYMENT_ID": "prod-mum-01",
            "DATA_MODE": "exact-production",
            "BACKUP_ROLE": "disabled",
        })
        errors = identity.validate()
        self.assertTrue(any("DATA_MODE" in e for e in errors))

    def test_s3_restore_conflicts_with_writer(self):
        identity = EnvironmentIdentity.from_env(environ={
            "APP_ENV": "staging",
            "DATA_MODE": "s3-restore",
            "RESTORE_SOURCE_DATASET_ID": "prod-dataset",
            "BACKUP_ROLE": "writer",
            "DATASET_ID": "staging-dataset",
        })
        errors = identity.validate()
        self.assertTrue(any(
            "writer" in e.lower() or "restore" in e.lower()
            for e in errors
        ), errors)


class SideEffectsTests(TestCase):
    def test_enabled_policy_email(self):
        identity = EnvironmentIdentity(
            app_env=AppEnv.PRODUCTION,
            external_side_effects=ExternalSideEffectsMode.ENABLED,
        )
        policy = SideEffectPolicy.from_identity(identity)
        self.assertTrue(policy.email_enabled)
        self.assertTrue(policy.payment_enabled)

    def test_disabled_policy(self):
        identity = EnvironmentIdentity(
            app_env=AppEnv.STAGING,
            external_side_effects=ExternalSideEffectsMode.DISABLED,
        )
        policy = SideEffectPolicy.from_identity(identity)
        self.assertFalse(policy.email_enabled)
        self.assertFalse(policy.payment_enabled)

    def test_sandbox_policy_email_console(self):
        identity = EnvironmentIdentity(
            app_env=AppEnv.STAGING,
            external_side_effects=ExternalSideEffectsMode.SANDBOX,
        )
        policy = SideEffectPolicy.from_identity(identity)
        self.assertTrue(policy.email_enabled)
        self.assertTrue("console" in policy.email_backend)
        self.assertFalse(policy.payment_enabled)

    def test_email_backend_resolution(self):
        identity = EnvironmentIdentity(
            app_env=AppEnv.PRODUCTION,
            external_side_effects=ExternalSideEffectsMode.ENABLED,
        )
        backend = resolve_email_backend(identity)
        self.assertIn("smtp", backend)

        identity_staging = EnvironmentIdentity(
            app_env=AppEnv.STAGING,
            external_side_effects=ExternalSideEffectsMode.DISABLED,
        )
        backend = resolve_email_backend(identity_staging)
        self.assertIn("dummy", backend)


class CompatibilityTests(TestCase):
    def test_empty_manifest_is_incompatible(self):
        report = check_generation_compatibility({})
        self.assertFalse(report.compatible)

    def test_unsupported_manifest_version(self):
        report = check_generation_compatibility({"manifest_version": 2})
        self.assertFalse(report.compatible)

    def test_embedding_model_mismatch(self):
        with self.settings(OPENAI_EMBED_MODEL="text-embedding-3-large"):
            report = check_generation_compatibility({
                "manifest_version": 1,
                "database": {"migrations": {"latest": "0019_sitesetting"}},
                "faiss": {"file_count": 0, "files": []},
                "embedding_index": {"model": "text-embedding-3-small"},
            })
        self.assertFalse(report.compatible)

    def test_sanitized_generation_warns(self):
        report = check_generation_compatibility({
            "manifest_version": 1,
            "database": {"migrations": {"latest": "0019_sitesetting"}},
            "faiss": {"file_count": 0, "files": []},
            "sanitization": {"policy_version": "pdfsearch-sanitize/v1"},
        })
        self.assertTrue(report.compatible)
        self.assertTrue(any("sanitized" in w.lower() for w in report.warnings))

    def test_same_embedding_model_passes(self):
        with self.settings(OPENAI_EMBED_MODEL="text-embedding-3-small"):
            report = check_generation_compatibility({
                "manifest_version": 1,
                "database": {"migrations": {"latest": "0019_sitesetting"}},
                "faiss": {"file_count": 0, "files": []},
                "embedding_index": {"model": "text-embedding-3-small"},
            })
        self.assertTrue(report.compatible)


from django.core.cache import cache as django_cache


class LegalPageTests(TestCase):
    """All legal/policy pages are publicly accessible and contain required content."""

    def setUp(self):
        super().setUp()

    LEGAL_ROUTES = [
        ("privacy", "Privacy Policy"),
        ("terms", "Terms of Service"),
        ("data_policy", "Data Policy"),
        ("cookie_policy", "Cookie Policy"),
        ("disclaimer", "Disclaimer"),
    ]

    def test_all_legal_pages_return_200(self):
        for name, _ in self.LEGAL_ROUTES:
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)

    def test_all_legal_pages_have_last_updated(self):
        for name, _ in self.LEGAL_ROUTES:
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertContains(response, "Last updated:")

    def test_privacy_policy_contains_dpda_references(self):
        response = self.client.get(reverse("privacy"))
        self.assertContains(response, "Data Fiduciary")
        self.assertContains(response, "Digital Personal Data Protection Act, 2023")
        self.assertContains(response, "Data Protection Board of India")
        self.assertContains(response, "Grievance Officer")
        self.assertContains(response, "openai.com/policies")

    def test_terms_of_service_distinguishes_user_types(self):
        response = self.client.get(reverse("terms"))
        self.assertContains(response, "Public Users")
        self.assertContains(response, "Authorized Officials")
        self.assertNotContains(response, "provide accurate and complete registration information")

    def test_terms_has_indian_jurisdiction(self):
        response = self.client.get(reverse("terms"))
        self.assertContains(response, "Pune, Maharashtra")

    def test_data_policy_has_retention_table(self):
        response = self.client.get(reverse("data_policy"))
        self.assertContains(response, "Data Classification")
        self.assertContains(response, "Retention Periods")
        self.assertContains(response, "Data Deletion Lifecycle")

    def test_cookie_policy_has_table_and_browser_links(self):
        response = self.client.get(reverse("cookie_policy"))
        self.assertContains(response, "Cookies We Set")
        self.assertContains(response, "sessionid")
        self.assertContains(response, "csrftoken")
        self.assertContains(response, "support.google.com/chrome")

    def test_cookie_policy_declares_no_tracking(self):
        response = self.client.get(reverse("cookie_policy"))
        self.assertContains(response, "do not use</strong>")
        self.assertContains(response, "tracking cookies")

    def test_disclaimer_has_ai_warning(self):
        response = self.client.get(reverse("disclaimer"))
        self.assertContains(response, "not legal advice")
        self.assertContains(response, "AI may produce incomplete or incorrect results")

    def test_disclaimer_has_official_source_precedence(self):
        response = self.client.get(reverse("disclaimer"))
        self.assertContains(response, "official records and gazette publications shall prevail")

    def test_homepage_has_legal_links(self):
        response = self.client.get(reverse("home"))
        for name, _ in self.LEGAL_ROUTES:
            url = reverse(name)
            self.assertContains(response, url)

    def test_footer_has_all_legal_links(self):
        response = self.client.get(reverse("privacy"))
        for name, _ in self.LEGAL_ROUTES:
            url = reverse(name)
            self.assertContains(response, url)

    def test_robots_txt_allows_all_legal_pages(self):
        response = self.client.get("/robots.txt")
        content = response.content.decode()
        for slug in ["privacy", "terms", "data-policy", "cookies", "disclaimer"]:
            with self.subTest(slug=slug):
                self.assertIn(f"Allow: /{slug}/", content)

    def test_sitemap_xml_includes_all_legal_pages(self):
        response = self.client.get("/sitemap.xml")
        content = response.content.decode()
        for slug in ["privacy", "terms", "data-policy", "cookies", "disclaimer"]:
            with self.subTest(slug=slug):
                self.assertIn(f"https://ai-sahakar.net/{slug}/", content)

    def test_cookie_banner_has_proper_links(self):
        response = self.client.get(reverse("home"))
        self.assertContains(response, "essential cookies")
        self.assertContains(response, reverse("cookie_policy"))
        self.assertContains(response, reverse("privacy"))

    def test_cookie_banner_uses_accessible_hallmark_region(self):
        response = self.client.get(reverse("home"))
        self.assertContains(response, 'role="region"')
        self.assertContains(response, 'aria-label="Cookie notice"')
        self.assertContains(response, "cookie-consent__accept")

    def test_legal_pages_extend_base_template(self):
        for name, _ in self.LEGAL_ROUTES:
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertContains(response, "Registrar Co-operative Societies")

    def test_legal_pages_use_public_legal_shell(self):
        for name, _ in self.LEGAL_ROUTES:
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertContains(response, 'class="legal-header"')
                self.assertContains(response, 'aria-label="Legal navigation"')
                self.assertNotContains(response, "Admin console")
                self.assertNotContains(response, 'class="admin-nav"')

    def test_footer_has_compact_legal_line(self):
        response = self.client.get(reverse("home"))
        self.assertContains(response, "All rights reserved")
        content = response.content.decode()
        self.assertIn("Privacy", content)
        self.assertIn("Terms", content)
        self.assertNotIn('class="footer-links"', content)

    def test_settings_page_shows_feature_flags_table(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        user = User.objects.create_superuser("settingstable", "settings@t.com", "Test@123", role="superadmin")
        self.client.force_login(user)
        response = self.client.get(reverse("settings"))
        self.assertContains(response, "Feature Flags")
        self.assertNotContains(response, "PUBLIC_UI_THEME")

    def test_settings_page_shows_safe_configuration_inventory(self):
        user = get_user_model().objects.create_superuser("settingsinventory", "inventory@t.com", "Test@123", role="superadmin")
        self.client.force_login(user)
        response = self.client.get(reverse("settings"))
        self.assertContains(response, "Configuration Inventory")
        self.assertContains(response, "PUBLIC_SEARCH_MAX_WORDS")
        self.assertContains(response, "Django secret key")
        self.assertContains(response, "Not configured")
        self.assertNotContains(response, os.environ.get("SECRET_KEY", "__secret_not_present__"))

    @patch.dict(os.environ, {"SETTINGS_EDIT_ENABLED": "1"})
    def test_settings_form_persists_named_runtime_setting(self):
        user = get_user_model().objects.create_superuser("settingswriter", "writer@t.com", "Test@123", role="superadmin")
        self.client.force_login(user)
        response = self.client.post(reverse("save_settings"), {"PUBLIC_SEARCH_ENABLED": "0"})
        self.assertRedirects(response, reverse("settings"))
        self.assertEqual(SiteSetting.objects.get(key="PUBLIC_SEARCH_ENABLED").value, "0")
