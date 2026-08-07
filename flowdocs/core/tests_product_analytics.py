from http.cookies import SimpleCookie
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.db import DatabaseError
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from core.models import SiteSetting
from core.product_analytics import (
    ANALYTICS_CONSENT_COOKIE,
    ANALYTICS_IDENTITY_COOKIE,
    ANALYTICS_PROFILES,
    CONSENT_DECLINED,
    CONSENT_GRANTED,
    PRODUCT_ANALYTICS_ENABLED,
    PRODUCT_ANALYTICS_MODE_SETTING,
    PRODUCT_ANALYTICS_WEBSITE_ID_SETTING,
    STAGE_UMAMI_WEBSITE_ID,
    UMAMI_SCRIPT_URL,
    analytics_profile_for_request,
    consent_state,
    set_product_analytics_mode,
)


STAGE_HOST = "2026.ai-sahakar.net"
PRODUCTION_HOST = "ai-sahakar.net"
WWW_HOST = "www.ai-sahakar.net"
PRODUCTION_WEBSITE_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


@override_settings(
    ALLOWED_HOSTS=["testserver", "localhost", STAGE_HOST, PRODUCTION_HOST, WWW_HOST],
    APP_RELEASE_VERSION="2026.08.07-test",
)
class ProductAnalyticsRenderingTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def enable(self, host=STAGE_HOST, website_id=""):
        return set_product_analytics_mode(
            PRODUCT_ANALYTICS_ENABLED,
            updated_by=None,
            profile=ANALYTICS_PROFILES[host],
            website_id=website_id,
        )

    def _cookie_header(self, response):
        return "; ".join(
            f"{name}={morsel.value}" for name, morsel in response.cookies.items()
        )

    def test_tracker_is_absent_by_default(self):
        response = self.client.get(reverse("home"), HTTP_HOST=STAGE_HOST)

        self.assertNotContains(response, "product-analytics-config")
        self.assertNotContains(response, "product-analytics.js")
        self.assertNotContains(response, STAGE_UMAMI_WEBSITE_ID)

    def test_local_and_unapproved_hosts_are_hard_disabled(self):
        self.enable()

        for host in ("testserver", "localhost"):
            with self.subTest(host=host):
                response = self.client.get(reverse("home"), HTTP_HOST=host)
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, "product-analytics-config")
                self.assertNotContains(response, "product-analytics.js")
                self.assertNotIn(ANALYTICS_CONSENT_COOKIE, response.cookies)
                self.assertNotIn(ANALYTICS_IDENTITY_COOKIE, response.cookies)

    def test_stage_and_production_are_separate_tenants(self):
        self.enable(STAGE_HOST)

        stage_response = self.client.get(reverse("home") + "?view=workbench", HTTP_HOST=STAGE_HOST)
        stage_config = stage_response.context["product_analytics_config"]
        self.assertEqual(stage_config["website_id"], STAGE_UMAMI_WEBSITE_ID)
        self.assertEqual(stage_config["deployment_tier"], "stage")
        self.assertEqual(stage_config["consent_status"], "unset")
        self.assertContains(stage_response, "Allow analytics")

        production_before_setup = self.client.get(reverse("home"), HTTP_HOST=PRODUCTION_HOST)
        self.assertNotContains(production_before_setup, "product-analytics-config")

        self.enable(PRODUCTION_HOST, PRODUCTION_WEBSITE_ID)
        production_response = self.client.get(reverse("home"), HTTP_HOST=PRODUCTION_HOST)
        production_config = production_response.context["product_analytics_config"]
        self.assertEqual(production_config["website_id"], PRODUCTION_WEBSITE_ID)
        self.assertEqual(production_config["deployment_tier"], "production")
        self.assertNotEqual(production_config["website_id"], stage_config["website_id"])
        self.assertTrue(
            SiteSetting.objects.filter(
                key=f"{PRODUCT_ANALYTICS_WEBSITE_ID_SETTING}:{PRODUCTION_HOST}",
                value=PRODUCTION_WEBSITE_ID,
            ).exists()
        )
        self.assertFalse(
            SiteSetting.objects.filter(key=PRODUCT_ANALYTICS_WEBSITE_ID_SETTING).exists()
        )

    def test_legacy_global_stage_toggle_cannot_enable_production(self):
        SiteSetting.objects.create(
            key=PRODUCT_ANALYTICS_MODE_SETTING,
            value=PRODUCT_ANALYTICS_ENABLED,
        )

        stage_response = self.client.get(reverse("home"), HTTP_HOST=STAGE_HOST)
        production_response = self.client.get(reverse("home"), HTTP_HOST=PRODUCTION_HOST)

        self.assertContains(stage_response, "product-analytics-config")
        self.assertNotContains(production_response, "product-analytics-config")

    def test_www_is_redirected_to_the_canonical_production_identity_host(self):
        self.enable(PRODUCTION_HOST, PRODUCTION_WEBSITE_ID)

        response = self.client.get("/?view=classic", HTTP_HOST=WWW_HOST)

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "https://ai-sahakar.net/?view=classic")
        self.assertNotIn(ANALYTICS_CONSENT_COOKIE, response.cookies)
        self.assertNotIn(ANALYTICS_IDENTITY_COOKIE, response.cookies)

    def test_www_redirects_before_any_route_can_create_a_split_session(self):
        response = self.client.get("/not-a-route/", HTTP_HOST=WWW_HOST)

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "https://ai-sahakar.net/not-a-route/")
        self.assertNotIn(ANALYTICS_CONSENT_COOKIE, response.cookies)
        self.assertNotIn(ANALYTICS_IDENTITY_COOKIE, response.cookies)

    def test_mode_lookup_database_failure_fails_closed(self):
        with patch(
            "core.product_analytics.SiteSetting.objects.filter",
            side_effect=DatabaseError("control database unavailable"),
        ):
            response = self.client.get(reverse("home"), HTTP_HOST=STAGE_HOST)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "product-analytics.js")

    def test_consent_issues_http_only_identity_and_exposes_only_derived_alias(self):
        self.enable()

        accepted = self.client.post(
            reverse("analytics_preference"),
            {"action": "accept", "next": "/?view=workbench"},
            HTTP_HOST=STAGE_HOST,
        )
        self.assertEqual(accepted.status_code, 302)
        self.assertEqual(accepted["Location"], "/?view=workbench")
        self.assertIn(ANALYTICS_CONSENT_COOKIE, accepted.cookies)
        self.assertIn(ANALYTICS_IDENTITY_COOKIE, accepted.cookies)
        for name in (ANALYTICS_CONSENT_COOKIE, ANALYTICS_IDENTITY_COOKIE):
            with self.subTest(cookie=name):
                morsel = accepted.cookies[name]
                self.assertTrue(morsel["httponly"])
                self.assertTrue(morsel["secure"])
                self.assertEqual(morsel["samesite"], "Lax")
                self.assertEqual(morsel["path"], "/")

        identity_token = accepted.cookies[ANALYTICS_IDENTITY_COOKIE].value
        response = self.client.get(
            reverse("home") + "?view=workbench",
            HTTP_HOST=STAGE_HOST,
            HTTP_COOKIE=self._cookie_header(accepted),
        )
        config = response.context["product_analytics_config"]
        self.assertEqual(config["consent_status"], CONSENT_GRANTED)
        self.assertTrue(config["identity_ready"])
        self.assertRegex(config["identity_alias"], r"^v1_[A-Za-z0-9_-]{43}$")
        self.assertNotEqual(config["identity_alias"], identity_token)
        self.assertNotContains(response, identity_token)

    def test_reset_rotates_alias_and_revoke_removes_identity_cookie(self):
        self.enable()
        accepted = self.client.post(
            reverse("analytics_preference"),
            {"action": "accept", "next": "/"},
            HTTP_HOST=STAGE_HOST,
        )
        initial_cookie_header = self._cookie_header(accepted)
        initial_request = self.factory.get("/", HTTP_HOST=STAGE_HOST, HTTP_COOKIE=initial_cookie_header)
        initial_profile = analytics_profile_for_request(initial_request)
        self.assertEqual(consent_state(initial_request), CONSENT_GRANTED)

        reset = self.client.post(
            reverse("analytics_preference"),
            {"action": "reset", "next": "/"},
            HTTP_HOST=STAGE_HOST,
            HTTP_COOKIE=initial_cookie_header,
        )
        reset_cookie_header = self._cookie_header(reset)
        self.assertNotEqual(
            accepted.cookies[ANALYTICS_IDENTITY_COOKIE].value,
            reset.cookies[ANALYTICS_IDENTITY_COOKIE].value,
        )
        reset_response = self.client.get(
            reverse("home"),
            HTTP_HOST=STAGE_HOST,
            HTTP_COOKIE=reset_cookie_header,
        )
        self.assertNotEqual(
            reset_response.context["product_analytics_config"]["identity_alias"],
            "",
        )
        self.assertIsNotNone(initial_profile)

        revoked = self.client.post(
            reverse("analytics_preference"),
            {"action": "revoke", "next": "/"},
            HTTP_HOST=STAGE_HOST,
            HTTP_COOKIE=reset_cookie_header,
        )
        self.assertEqual(revoked.cookies[ANALYTICS_IDENTITY_COOKIE]["max-age"], 0)
        revoked_response = self.client.get(
            reverse("home"),
            HTTP_HOST=STAGE_HOST,
            HTTP_COOKIE=self._cookie_header(revoked),
        )
        self.assertEqual(
            revoked_response.context["product_analytics_config"]["consent_status"],
            "declined",
        )
        self.assertEqual(
            revoked_response.context["product_analytics_config"]["identity_alias"],
            "",
        )

    def test_preference_endpoint_cannot_set_local_identity(self):
        response = self.client.post(
            reverse("analytics_preference"),
            {"action": "accept", "next": "/"},
            HTTP_HOST="localhost",
        )

        self.assertEqual(response.status_code, 404)
        self.assertNotIn(ANALYTICS_CONSENT_COOKIE, response.cookies)
        self.assertNotIn(ANALYTICS_IDENTITY_COOKIE, response.cookies)

    def test_global_privacy_control_prevents_identity_issuance_and_clears_a_prior_id(self):
        self.enable()
        accepted = self.client.post(
            reverse("analytics_preference"),
            {"action": "accept", "next": "/"},
            HTTP_HOST=STAGE_HOST,
        )
        accepted_cookie_header = self._cookie_header(accepted)

        response = self.client.get(
            reverse("home"),
            HTTP_HOST=STAGE_HOST,
            HTTP_SEC_GPC="1",
            HTTP_COOKIE=accepted_cookie_header,
        )

        config = response.context["product_analytics_config"]
        self.assertTrue(config["gpc_blocked"])
        self.assertFalse(config["identity_ready"])
        self.assertEqual(config["identity_alias"], "")
        self.assertEqual(response.cookies[ANALYTICS_IDENTITY_COOKIE]["max-age"], 0)
        self.assertContains(response, "Analytics is off for this browser")

    def test_global_privacy_control_rejects_an_analytics_opt_in(self):
        self.enable()

        response = self.client.post(
            reverse("analytics_preference"),
            {"action": "accept", "next": "/"},
            HTTP_HOST=STAGE_HOST,
            HTTP_SEC_GPC="1",
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(ANALYTICS_CONSENT_COOKIE, response.cookies)
        self.assertEqual(response.cookies[ANALYTICS_IDENTITY_COOKIE]["max-age"], 0)
        blocked = self.client.get(
            reverse("home"),
            HTTP_HOST=STAGE_HOST,
            HTTP_SEC_GPC="1",
            HTTP_COOKIE=self._cookie_header(response),
        )
        self.assertEqual(blocked.context["product_analytics_config"]["consent_status"], CONSENT_DECLINED)

    def test_invalid_website_id_is_rejected_before_mode_write(self):
        with self.assertRaisesRegex(ValueError, "valid UUID"):
            set_product_analytics_mode(
                PRODUCT_ANALYTICS_ENABLED,
                updated_by=None,
                profile=ANALYTICS_PROFILES[PRODUCTION_HOST],
                website_id="not-a-uuid",
            )
        self.assertFalse(
            SiteSetting.objects.filter(
                key=f"{PRODUCT_ANALYTICS_MODE_SETTING}:{PRODUCTION_HOST}"
            ).exists()
        )

    def test_tracker_source_enforces_consent_identity_and_content_free_transport(self):
        source_path = finders.find("main/js/product-analytics.js")
        self.assertIsNotNone(source_path)
        source = Path(source_path).read_text(encoding="utf-8")

        for required in (
            'script.async = true',
            'script.dataset.autoTrack = "false"',
            'script.dataset.doNotTrack = "false"',
            'script.dataset.beforeSend = "aiSahakarAnalyticsBeforeSend"',
            'script.dataset.excludeSearch = "true"',
            'script.dataset.excludeHash = "true"',
            'script.dataset.performance = "false"',
            'type === "identify"',
            "window.umami.identify(config.identity_alias)",
            'url: `/product-events/${config.surface}`',
            "navigator.globalPrivacyControl === true",
        ):
            with self.subTest(required=required):
                self.assertIn(required, source)
        self.assertNotIn("localStorage", source)
        self.assertNotIn("sessionStorage", source)
        self.assertNotIn("document.cookie", source)

    def test_search_clients_defer_after_the_privacy_adapter(self):
        """Document order is deliberate: clients need the adapter at startup."""

        self.enable()
        cases = (
            ("workbench", "search.js"),
            ("classic", "search-classic.js"),
        )
        adapter_tag = '<script defer src="/static/main/js/product-analytics.js"></script>'

        for view, client_name in cases:
            with self.subTest(view=view):
                response = self.client.get(
                    reverse("home") + f"?view={view}",
                    HTTP_HOST=STAGE_HOST,
                )
                html = response.content.decode("utf-8")
                client_tag = f'<script defer src="/static/main/js/{client_name}"></script>'

                self.assertContains(response, adapter_tag, html=True)
                self.assertContains(response, client_tag, html=True)
                self.assertLess(html.index(adapter_tag), html.index(client_tag))


@override_settings(ALLOWED_HOSTS=["testserver", STAGE_HOST, PRODUCTION_HOST])
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

    def test_settings_exposes_host_scoped_configuration(self):
        self.client.force_login(self.superadmin)

        response = self.client.get(reverse("settings"), HTTP_HOST=STAGE_HOST)

        self.assertContains(response, "Consent-led product analytics")
        self.assertContains(response, "Analytics collection for this host")
        self.assertContains(response, STAGE_HOST)
        self.assertContains(response, STAGE_UMAMI_WEBSITE_ID)
        self.assertContains(response, "Approved stage default")
        self.assertContains(response, 'value="disabled" checked')

    def test_superadmin_saves_stage_and_production_under_different_keys(self):
        self.client.force_login(self.superadmin)

        stage_response = self.client.post(
            reverse("save_product_analytics"),
            {
                "product_analytics_mode": "enabled",
                "product_analytics_website_id": STAGE_UMAMI_WEBSITE_ID,
                "confirm_persistent_analytics": "yes",
            },
            HTTP_HOST=STAGE_HOST,
        )
        self.assertRedirects(stage_response, reverse("settings"))
        production_response = self.client.post(
            reverse("save_product_analytics"),
            {
                "product_analytics_mode": "enabled",
                "product_analytics_website_id": PRODUCTION_WEBSITE_ID,
                "confirm_persistent_analytics": "yes",
            },
            HTTP_HOST=PRODUCTION_HOST,
        )
        self.assertRedirects(production_response, reverse("settings"))

        self.assertEqual(
            SiteSetting.objects.get(key=f"{PRODUCT_ANALYTICS_MODE_SETTING}:{STAGE_HOST}").value,
            "enabled",
        )
        self.assertEqual(
            SiteSetting.objects.get(key=f"{PRODUCT_ANALYTICS_MODE_SETTING}:{PRODUCTION_HOST}").value,
            "enabled",
        )
        self.assertEqual(
            SiteSetting.objects.get(key=f"{PRODUCT_ANALYTICS_WEBSITE_ID_SETTING}:{PRODUCTION_HOST}").value,
            PRODUCTION_WEBSITE_ID,
        )

    def test_enabling_requires_confirmation_and_production_id(self):
        self.client.force_login(self.superadmin)

        no_confirmation = self.client.post(
            reverse("save_product_analytics"),
            {"product_analytics_mode": "enabled"},
            HTTP_HOST=STAGE_HOST,
        )
        self.assertRedirects(no_confirmation, reverse("settings"))
        self.assertFalse(
            SiteSetting.objects.filter(key=f"{PRODUCT_ANALYTICS_MODE_SETTING}:{STAGE_HOST}").exists()
        )

        no_production_id = self.client.post(
            reverse("save_product_analytics"),
            {"product_analytics_mode": "enabled", "confirm_persistent_analytics": "yes"},
            HTTP_HOST=PRODUCTION_HOST,
        )
        self.assertRedirects(no_production_id, reverse("settings"))
        self.assertFalse(
            SiteSetting.objects.filter(key=f"{PRODUCT_ANALYTICS_MODE_SETTING}:{PRODUCTION_HOST}").exists()
        )

    def test_local_settings_are_visibly_hard_disabled_and_cannot_save(self):
        self.client.force_login(self.superadmin)

        response = self.client.get(reverse("settings"), HTTP_HOST="testserver")
        self.assertContains(response, "Hard-disabled on this host")
        save = self.client.post(
            reverse("save_product_analytics"),
            {"product_analytics_mode": "enabled", "confirm_persistent_analytics": "yes"},
            HTTP_HOST="testserver",
        )
        self.assertRedirects(save, reverse("settings"))
        self.assertFalse(SiteSetting.objects.filter(key__startswith=PRODUCT_ANALYTICS_MODE_SETTING).exists())

    def test_non_superadmin_cannot_change_mode(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("save_product_analytics"),
            {"product_analytics_mode": "enabled", "confirm_persistent_analytics": "yes"},
            HTTP_HOST=STAGE_HOST,
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(SiteSetting.objects.filter(key__startswith=PRODUCT_ANALYTICS_MODE_SETTING).exists())
