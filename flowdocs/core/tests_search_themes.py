from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse

from core.models import SiteSetting
from core.search_ui import PRIMARY_SEARCH_VIEW_SETTING


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
