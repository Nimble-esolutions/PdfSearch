from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import SiteSetting
from core.product_analytics import (
    PRODUCT_ANALYTICS_ENABLED,
    PRODUCT_ANALYTICS_MODE_SETTING,
    UMAMI_SCRIPT_URL,
    UMAMI_WEBSITE_ID,
)


@override_settings(
    ALLOWED_HOSTS=["testserver", "2026.ai-sahakar.net", "ai-sahakar.net"],
    APP_RELEASE_VERSION="2026.08.07-test",
)
class ProductAnalyticsRenderingTests(TestCase):
    def enable(self):
        SiteSetting.objects.update_or_create(
            key=PRODUCT_ANALYTICS_MODE_SETTING,
            defaults={"value": PRODUCT_ANALYTICS_ENABLED},
        )

    def test_tracker_is_absent_by_default(self):
        response = self.client.get(reverse("home"), HTTP_HOST="2026.ai-sahakar.net")

        self.assertNotContains(response, "product-analytics-config")
        self.assertNotContains(response, "product-analytics.js")
        self.assertNotContains(response, UMAMI_WEBSITE_ID)

    def test_enabled_tracker_is_restricted_to_exact_stage_host(self):
        self.enable()

        for host in ("testserver", "ai-sahakar.net"):
            with self.subTest(host=host):
                response = self.client.get(reverse("home"), HTTP_HOST=host)
                self.assertNotContains(response, "product-analytics.js")
                self.assertNotContains(response, UMAMI_WEBSITE_ID)

        response = self.client.get(
            reverse("home") + "?view=workbench",
            HTTP_HOST="2026.ai-sahakar.net",
        )
        self.assertContains(response, "product-analytics-config")
        self.assertContains(response, "product-analytics.js")
        self.assertContains(response, UMAMI_WEBSITE_ID)
        self.assertContains(response, UMAMI_SCRIPT_URL)
        self.assertEqual(response.context["product_analytics_config"]["surface"], "workbench")
        self.assertTrue(response.context["product_analytics_config"]["view_override_used"])

    def test_non_stage_host_short_circuits_before_mode_lookup(self):
        with patch("core.product_analytics.SiteSetting.objects.filter") as mode_lookup:
            response = self.client.get(reverse("home"), HTTP_HOST="ai-sahakar.net")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "product-analytics.js")
        mode_lookup.assert_not_called()

    def test_mode_lookup_database_failure_fails_closed(self):
        with patch(
            "core.product_analytics.SiteSetting.objects.filter",
            side_effect=DatabaseError("control database unavailable"),
        ):
            response = self.client.get(
                reverse("home"),
                HTTP_HOST="2026.ai-sahakar.net",
            )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "product-analytics.js")

    def test_tracker_source_enforces_manual_fail_open_privacy_attributes(self):
        source_path = finders.find("main/js/product-analytics.js")
        self.assertIsNotNone(source_path)
        source = Path(source_path).read_text(encoding="utf-8")

        for required in (
            'script.async = true',
            'script.dataset.autoTrack = "false"',
            'script.dataset.doNotTrack = "true"',
            'script.dataset.beforeSend = "aiSahakarAnalyticsBeforeSend"',
            'script.dataset.excludeSearch = "true"',
            'script.dataset.excludeHash = "true"',
            'script.dataset.performance = "false"',
            'type !== "event"',
            'url: `/product-events/${config.surface}`',
        ):
            with self.subTest(required=required):
                self.assertIn(required, source)
        self.assertNotIn("window.umami.identify", source)
        self.assertNotIn("localStorage", source)
        self.assertNotIn("sessionStorage", source)


class ProductAnalyticsAdminTests(TestCase):
    def setUp(self):
        self.superadmin = get_user_model().objects.create_superuser(
            "analytics-superadmin",
            "analytics-superadmin@example.test",
            "Test@123",
            role="superadmin",
        )
        self.admin = get_user_model().objects.create_user(
            username="analytics-admin",
            password="Test@123",
            role="admin",
        )

    def test_settings_exposes_explicit_disabled_default(self):
        self.client.force_login(self.superadmin)

        response = self.client.get(reverse("settings"))

        self.assertContains(response, "Privacy-bounded product analytics")
        self.assertContains(response, 'value="disabled" checked')
        self.assertContains(response, "2026.ai-sahakar.net")

    def test_superadmin_can_enable_and_disable_without_environment_gate(self):
        self.client.force_login(self.superadmin)

        for mode in ("enabled", "disabled"):
            with self.subTest(mode=mode):
                response = self.client.post(
                    reverse("save_product_analytics"),
                    {"product_analytics_mode": mode},
                )
                self.assertRedirects(response, reverse("settings"))
                setting = SiteSetting.objects.get(key=PRODUCT_ANALYTICS_MODE_SETTING)
                self.assertEqual(setting.value, mode)
                self.assertEqual(setting.updated_by, self.superadmin)

    def test_invalid_mode_fails_closed(self):
        self.client.force_login(self.superadmin)

        response = self.client.post(
            reverse("save_product_analytics"),
            {"product_analytics_mode": "capture-everything"},
        )

        self.assertRedirects(response, reverse("settings"))
        self.assertFalse(
            SiteSetting.objects.filter(key=PRODUCT_ANALYTICS_MODE_SETTING).exists()
        )

    def test_non_superadmin_cannot_change_mode(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("save_product_analytics"),
            {"product_analytics_mode": "enabled"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            SiteSetting.objects.filter(key=PRODUCT_ANALYTICS_MODE_SETTING).exists()
        )
