from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.cache import cache
from django.db import DatabaseError
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from unittest.mock import patch

from core.models import SiteSetting
from core.search_ui import PRIMARY_SEARCH_VIEW_SETTING
from core.views import (
    public_bad_request,
    public_permission_denied,
    public_server_error,
)


class SearchThemeResolutionTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_classic_is_the_safe_default_and_frontends_are_isolated(self):
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "search_classic.html")
        self.assertEqual(response.context["search_view"], "classic")
        self.assertContains(response, "search-classic.css")
        self.assertContains(response, "search-classic.js")
        self.assertNotContains(response, "cookieConsent")
        self.assertNotContains(response, "civic-workbench.css")
        self.assertNotContains(response, 'main/js/search.js')
        self.assertNotContains(response, "bootstrap")

    def test_valid_shareable_override_does_not_persist(self):
        response = self.client.get(reverse("home") + "?view=workbench")

        self.assertTemplateUsed(response, "search.html")
        self.assertEqual(response.context["search_view"], "workbench")
        self.assertContains(response, "civic-workbench.css")
        self.assertNotContains(response, "search-classic.css")
        self.assertFalse(
            SiteSetting.objects.filter(key=PRIMARY_SEARCH_VIEW_SETTING).exists()
        )

    def test_invalid_override_falls_back_to_persisted_primary_view(self):
        SiteSetting.objects.create(
            key=PRIMARY_SEARCH_VIEW_SETTING,
            value="workbench",
        )
        cache.clear()

        response = self.client.get(reverse("home") + "?view=unsupported")

        self.assertTemplateUsed(response, "search.html")
        self.assertEqual(response.context["search_view"], "workbench")

    def test_invalid_persisted_value_fails_closed_to_classic(self):
        SiteSetting.objects.create(
            key=PRIMARY_SEARCH_VIEW_SETTING,
            value="unknown",
        )
        cache.clear()

        response = self.client.get(reverse("home"))

        self.assertTemplateUsed(response, "search_classic.html")

    def test_supplied_public_links_are_rendered_in_both_views(self):
        expected_map_id = "1MoHzbONhTORm8fQ0IAZCWG82vtUgIJU"
        expected_feedback_id = "1FAIpQLSca6zWE0E5CIwoFfT6lzdGhaqaslLFpjaSu2mK654hAQVDlSg"
        expected_help_id = "1K4Z0RnRcQFXXDxxO10xFVjAbFRBXu7errbWbqtIK8qE"

        for suffix in ("", "?view=workbench"):
            with self.subTest(suffix=suffix or "classic"):
                response = self.client.get(reverse("home") + suffix)
                self.assertContains(response, expected_map_id)
                self.assertContains(response, expected_feedback_id)
                self.assertContains(response, expected_help_id)

    def test_language_form_preserves_shareable_view_override(self):
        response = self.client.get(reverse("home") + "?view=workbench")

        self.assertContains(response, 'value="/?view=workbench"')

    def test_legacy_search_url_preserves_valid_shareable_override(self):
        response = self.client.get(reverse("search_query") + "?view=workbench")

        self.assertRedirects(
            response,
            reverse("home") + "?view=workbench",
            status_code=301,
            fetch_redirect_response=False,
        )

    def test_policy_pages_follow_persisted_workbench_theme(self):
        SiteSetting.objects.create(
            key=PRIMARY_SEARCH_VIEW_SETTING,
            value="workbench",
        )
        cache.clear()

        for route in ("privacy", "terms", "data_policy", "cookie_policy", "disclaimer"):
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                self.assertEqual(response.context["search_view"], "workbench")
                self.assertContains(response, 'class="public-legal public-legal--workbench"')
                self.assertContains(response, 'class="workbench-header"')
                self.assertNotContains(response, 'class="classic-banner"')
                self.assertNotContains(response, "data-about-open")

    def test_policy_preview_override_is_allowlisted_and_preserved(self):
        response = self.client.get(reverse("privacy") + "?view=workbench")

        self.assertEqual(response.context["search_view"], "workbench")
        self.assertContains(response, 'href="/?view=workbench"')
        self.assertContains(response, 'href="/terms/?view=workbench"')
        self.assertContains(
            response,
            '<link rel="canonical" href="https://ai-sahakar.net/privacy/">',
            html=True,
        )
        self.assertNotContains(
            response,
            'rel="canonical" href="https://ai-sahakar.net/privacy/?view=workbench"',
        )

    def test_invalid_policy_preview_falls_back_without_reflecting_query(self):
        SiteSetting.objects.create(
            key=PRIMARY_SEARCH_VIEW_SETTING,
            value="workbench",
        )
        cache.clear()

        response = self.client.get(reverse("privacy") + "?view=arbitrary-template")

        self.assertEqual(response.context["search_view"], "workbench")
        self.assertContains(response, 'class="public-legal public-legal--workbench"')
        self.assertNotContains(response, "arbitrary-template")
        self.assertNotContains(response, "/terms/?view=")

    def test_classic_policy_page_is_standalone_and_keeps_one_page_heading(self):
        response = self.client.get(reverse("terms"))
        content = response.content.decode()

        self.assertContains(response, 'class="classic-banner"')
        self.assertContains(response, 'class="legal-document" lang="en"')
        self.assertEqual(content.count("<h1"), 1)
        self.assertNotContains(response, "bootstrap")
        self.assertNotContains(response, "main/css/style.css")

    def test_marathi_interface_does_not_mislabel_english_policy_copy(self):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = "mr"

        response = self.client.get(reverse("privacy") + "?view=workbench")

        self.assertContains(response, '<html lang="mr">')
        self.assertContains(response, '<article class="legal-document" lang="en">')
        self.assertContains(response, 'value="/privacy/?view=workbench"')


@override_settings(DEBUG=False)
class PublicNotFoundThemeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()

    def test_not_found_uses_the_safe_classic_public_shell_by_default(self):
        response = self.client.get("/not-a-public-route/")

        self.assertEqual(response.status_code, 404)
        self.assertTemplateUsed(response, "public_error.html")
        self.assertEqual(response.context["search_view"], "classic")
        self.assertContains(response, 'class="classic-search public-error public-error--classic"', status_code=404)
        self.assertContains(response, 'class="classic-banner"', status_code=404)
        self.assertContains(response, 'href="/"', status_code=404)
        self.assertContains(response, 'href="/login/"', status_code=404)
        self.assertContains(response, '<meta name="robots" content="noindex, nofollow, noarchive">', html=True, status_code=404)
        self.assertNotContains(response, 'rel="canonical"', status_code=404)
        self.assertContains(response, 'class="public-error__sr-only">Error 404</span>', status_code=404)
        self.assertNotContains(response, 'class="public-error__code" aria-label=', status_code=404)
        self.assertNotContains(response, "not-a-public-route", status_code=404)
        self.assertNotContains(response, "product-analytics-config", status_code=404)
        self.assertNotContains(response, "search-classic.js", status_code=404)

    def test_not_found_honors_the_allowlisted_workbench_preview(self):
        response = self.client.get("/not-a-public-route/?view=workbench")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.context["search_view"], "workbench")
        self.assertContains(response, 'class="public-search public-error public-error--workbench"', status_code=404)
        self.assertContains(response, 'class="workbench-header"', status_code=404)
        self.assertContains(response, 'href="/?view=workbench"', status_code=404)
        self.assertNotContains(response, "not-a-public-route", status_code=404)
        self.assertNotContains(response, "product-analytics.js", status_code=404)
        self.assertNotContains(response, "main/js/search.js", status_code=404)

    def test_related_public_errors_use_the_same_safe_theme_shell(self):
        handlers = {
            400: lambda request: public_bad_request(request, Exception()),
            403: lambda request: public_permission_denied(request, Exception()),
            500: public_server_error,
        }

        for status, handler in handlers.items():
            with self.subTest(status=status):
                request = self.factory.get(
                    "/failed-request/?view=workbench",
                    HTTP_HOST="testserver",
                )
                response = handler(request)

                self.assertEqual(response.status_code, status)
                self.assertContains(
                    response,
                    'class="public-search public-error public-error--workbench"',
                    status_code=status,
                )
                self.assertContains(response, f">{status}</span>", status_code=status)
                self.assertNotContains(response, "failed-request", status_code=status)
                self.assertNotContains(response, "product-analytics-config", status_code=status)
                self.assertNotContains(response, "main/js/search.js", status_code=status)

    def test_project_routes_all_standard_error_handlers_to_the_public_renderer(self):
        from flowdocs import urls as project_urls

        self.assertEqual(project_urls.handler400, "core.views.public_bad_request")
        self.assertEqual(project_urls.handler403, "core.views.public_permission_denied")
        self.assertEqual(project_urls.handler404, "core.views.public_page_not_found")
        self.assertEqual(project_urls.handler500, "core.views.public_server_error")

    @override_settings(ROOT_URLCONF="core.error_test_urls")
    def test_unhandled_server_error_uses_the_safe_public_renderer(self):
        self.client.raise_request_exception = False

        response = self.client.get("/intentional-server-error/?view=workbench")

        self.assertEqual(response.status_code, 500)
        self.assertTemplateUsed(response, "public_error.html")
        self.assertContains(
            response,
            'class="public-search public-error public-error--workbench"',
            status_code=500,
        )
        self.assertContains(response, 'href="/login/"', status_code=500)
        self.assertContains(response, 'action="/i18n/setlang/"', status_code=500)
        self.assertContains(response, 'href="/privacy/?view=workbench"', status_code=500)
        self.assertNotContains(response, "intentional public error renderer test", status_code=500)
        self.assertNotContains(response, "product-analytics-config", status_code=500)

    def test_not_found_uses_the_persisted_workbench_primary_view(self):
        SiteSetting.objects.create(
            key=PRIMARY_SEARCH_VIEW_SETTING,
            value="workbench",
        )
        cache.clear()

        response = self.client.get("/another-missing-route/")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.context["search_view"], "workbench")
        self.assertContains(response, 'class="workbench-header"', status_code=404)
        self.assertContains(response, 'href="/"', status_code=404)

    def test_not_found_remains_recoverable_if_persisted_theme_lookup_fails(self):
        with patch("core.views.get_primary_search_view", side_effect=DatabaseError):
            response = self.client.get("/another-missing-route/?view=workbench")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.context["search_view"], "workbench")
        self.assertContains(response, 'class="workbench-header"', status_code=404)
        self.assertNotContains(response, "product-analytics-config", status_code=404)

    def test_not_found_uses_a_static_public_header_for_signed_in_visitors(self):
        user = get_user_model().objects.create_superuser(
            "error-page-user",
            "error-page-user@example.test",
            "Test@123",
            role="superadmin",
        )
        self.client.force_login(user)

        response = self.client.get("/another-missing-route/?view=workbench")

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Admin login", status_code=404)
        self.assertNotContains(response, "error-page-user", status_code=404)
        self.assertNotContains(response, ">Dashboard<", status_code=404)

    def test_not_found_translates_its_recovery_controls_for_both_marathi_shells(self):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = "mr"

        for view in ("classic", "workbench"):
            with self.subTest(view=view):
                response = self.client.get(f"/another-missing-route/?view={view}")

                self.assertEqual(response.status_code, 404)
                self.assertContains(response, '<html lang="mr">', status_code=404)
                self.assertContains(response, "सेवा सूचना", status_code=404)
                self.assertContains(response, "दस्तऐवज शोधाकडे परत जा", status_code=404)


class SearchThemeAdminTests(TestCase):
    def setUp(self):
        self.superadmin = get_user_model().objects.create_superuser(
            "theme-superadmin",
            "theme-superadmin@example.test",
            "Test@123",
            role="superadmin",
        )
        self.admin = get_user_model().objects.create_user(
            username="theme-admin",
            password="Test@123",
            role="admin",
        )
        cache.clear()

    def test_settings_exposes_primary_view_control_to_superadmin(self):
        self.client.force_login(self.superadmin)

        response = self.client.get(reverse("settings"))

        self.assertContains(response, "Public search presentation")
        self.assertContains(response, 'value="classic" checked')
        self.assertContains(response, "?view=workbench")

    def test_superadmin_can_change_primary_view_without_env_gate(self):
        self.client.force_login(self.superadmin)

        response = self.client.post(
            reverse("save_search_ui"),
            {"primary_search_view": "workbench"},
        )

        self.assertRedirects(response, reverse("settings"))
        setting = SiteSetting.objects.get(key=PRIMARY_SEARCH_VIEW_SETTING)
        self.assertEqual(setting.value, "workbench")
        self.assertEqual(setting.updated_by, self.superadmin)
        self.assertTemplateUsed(self.client.get(reverse("home")), "search.html")

    def test_invalid_value_is_rejected_without_changing_primary_view(self):
        SiteSetting.objects.create(
            key=PRIMARY_SEARCH_VIEW_SETTING,
            value="classic",
            updated_by=self.superadmin,
        )
        self.client.force_login(self.superadmin)

        response = self.client.post(
            reverse("save_search_ui"),
            {"primary_search_view": "arbitrary-template"},
        )

        self.assertRedirects(response, reverse("settings"))
        self.assertEqual(
            SiteSetting.objects.get(key=PRIMARY_SEARCH_VIEW_SETTING).value,
            "classic",
        )

    def test_non_superadmin_cannot_change_primary_view(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("save_search_ui"),
            {"primary_search_view": "workbench"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            SiteSetting.objects.filter(key=PRIMARY_SEARCH_VIEW_SETTING).exists()
        )

    def test_theme_selection_requires_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.superadmin)

        response = csrf_client.post(
            reverse("save_search_ui"),
            {"primary_search_view": "workbench"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            SiteSetting.objects.filter(key=PRIMARY_SEARCH_VIEW_SETTING).exists()
        )
