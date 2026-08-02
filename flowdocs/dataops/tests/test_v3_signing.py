from django.test import SimpleTestCase, override_settings

from dataops.v3_signing import V3SigningError, manifest_signing_material


class ManifestSigningMaterialTests(SimpleTestCase):
    @override_settings(
        APP_ENV="development",
        ACTIVATION_INTENT_SIGNING_KEY="",
        SECRET_KEY="local-existing-secret",
    )
    def test_development_reuses_existing_application_secret(self):
        key, key_id = manifest_signing_material()
        self.assertEqual(key, b"local-existing-secret")
        self.assertTrue(key_id.startswith("dataops-manifest-"))

    @override_settings(
        APP_ENV="staging",
        ACTIVATION_INTENT_SIGNING_KEY="",
        SECRET_KEY="must-not-be-used-in-staging",
    )
    def test_staging_fails_closed_without_explicit_key(self):
        with self.assertRaisesMessage(
            V3SigningError,
            "manifest_signing_key_missing",
        ):
            manifest_signing_material()

    @override_settings(
        APP_ENV="production",
        ACTIVATION_INTENT_SIGNING_KEY="dedicated-stable-key",
        SECRET_KEY="application-secret",
    )
    def test_production_uses_explicit_stable_key(self):
        key, _key_id = manifest_signing_material()
        self.assertEqual(key, b"dedicated-stable-key")
