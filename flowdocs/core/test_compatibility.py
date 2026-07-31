from django.test import SimpleTestCase

from core.compatibility import CompatibilityReport, _check_release_identity


class RepackedReleaseCompatibilityTests(SimpleTestCase):
    def manifest(self, **overrides):
        values = {
            "app_release": "producer-release",
            "image_digest": "sha256:producer-image",
            "read_only": True,
            "repacked_from_generation_id": "legacy-generation",
        }
        values.update(overrides)
        return values

    def check(self, manifest, *, allow=False, compatible=True):
        report = CompatibilityReport(compatible=compatible)
        _check_release_identity(
            manifest,
            report,
            app_release="running-release",
            image_digest="sha256:running-image",
            allow_repacked_release_mismatch=allow,
        )
        return report

    def test_release_mismatch_remains_default_deny(self):
        report = self.check(self.manifest())

        self.assertFalse(report.compatible)
        self.assertFalse(report.checks["repacked_release_override"])
        self.assertTrue(report.errors)

    def test_guarded_override_accepts_read_only_repacked_generation(self):
        report = self.check(self.manifest(), allow=True)

        self.assertTrue(report.compatible)
        self.assertTrue(report.checks["repacked_release_override"])
        self.assertTrue(report.warnings)
        self.assertFalse(report.errors)

    def test_override_rejects_generation_without_repack_provenance(self):
        report = self.check(
            self.manifest(repacked_from_generation_id=""), allow=True
        )

        self.assertFalse(report.compatible)
        self.assertFalse(report.checks["repacked_release_override"])

    def test_override_never_erases_structural_incompatibility(self):
        report = self.check(self.manifest(), allow=True, compatible=False)

        self.assertFalse(report.compatible)
        self.assertTrue(report.checks["repacked_release_override"])
