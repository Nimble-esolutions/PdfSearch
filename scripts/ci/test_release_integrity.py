import contextlib
import io
import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.ops import release_integrity
from scripts.ops import verify_running_release


ROOT = Path(__file__).resolve().parents[2]


def make_source_tree(root: Path) -> None:
    files = {
        "flowdocs/dataops/models.py": "class RecoveryPoint:\n    pass\n",
        "flowdocs/dataops/migrations/__init__.py": "",
        "flowdocs/dataops/migrations/0001_initial.py": (
            "class Migration:\n    dependencies = []\n"
        ),
        "flowdocs/dataops/migrations/0002_control.py": (
            "class Migration:\n"
            "    dependencies = [('dataops', '0001_initial')]\n"
        ),
        "docker-entrypoint.sh": "#!/bin/sh\n",
        "worker-entrypoint.sh": "#!/bin/sh\n",
        "start.sh": "#!/bin/sh\n",
        "scripts/ops/release_integrity.py": "# verifier\n",
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def make_control_database(path: Path, migrations: list[str]) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE django_migrations "
            "(id INTEGER PRIMARY KEY, app TEXT NOT NULL, name TEXT NOT NULL, applied TEXT)"
        )
        connection.executemany(
            "INSERT INTO django_migrations(app, name, applied) VALUES('dataops', ?, '')",
            [(name,) for name in migrations],
        )


class ReleaseManifestTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        make_source_tree(self.root)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_manifest_covers_models_entrypoints_and_migration_leaf(self):
        manifest = release_integrity.build_manifest(self.root)
        self.assertEqual(
            manifest["dataops_migrations"]["leaves"], ["0002_control"]
        )
        sentinels = manifest["sentinels"]
        for required in (
            "flowdocs/dataops/models.py",
            "flowdocs/dataops/migrations/0002_control.py",
            "docker-entrypoint.sh",
            "worker-entrypoint.sh",
            "start.sh",
        ):
            with self.subTest(required=required):
                self.assertIn(required, sentinels)

    def test_verify_rejects_changed_packaged_source(self):
        manifest_path = self.root / "release-integrity.json"
        manifest_path.write_text(
            json.dumps(release_integrity.build_manifest(self.root)), encoding="utf-8"
        )
        (self.root / "flowdocs/dataops/models.py").write_text(
            "class NewerModel:\n    pass\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(
            release_integrity.IntegrityError,
            "sentinel:flowdocs/dataops/models.py",
        ):
            release_integrity.verify_manifest(self.root, manifest_path)


class StartupCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        make_source_tree(self.root)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_absent_control_database_is_allowed_without_creation(self):
        database = self.root / "control.sqlite3"
        status = release_integrity.check_startup_compatibility(database, self.root)
        self.assertEqual(status, "control_database_absent")
        self.assertFalse(database.exists())

    def test_known_applied_migrations_are_compatible_and_read_only(self):
        database = self.root / "control.sqlite3"
        make_control_database(database, ["0001_initial", "0002_control"])
        before = database.read_bytes()
        status = release_integrity.check_startup_compatibility(database, self.root)
        self.assertEqual(status, "compatible")
        self.assertEqual(database.read_bytes(), before)

    def test_newer_applied_migration_fails_without_mutating_database(self):
        database = self.root / "control.sqlite3"
        make_control_database(database, ["0001_initial", "0002_control", "0003_newer"])
        before = database.read_bytes()
        with self.assertRaisesRegex(
            release_integrity.IntegrityError,
            "startup_schema_contract_older_than_control_database",
        ):
            release_integrity.check_startup_compatibility(database, self.root)
        self.assertEqual(database.read_bytes(), before)


class EntrypointContractTests(unittest.TestCase):
    def test_web_and_worker_gate_before_persistent_directory_mutation(self):
        for entrypoint in ("docker-entrypoint.sh", "worker-entrypoint.sh"):
            text = (ROOT / entrypoint).read_text(encoding="utf-8")
            with self.subTest(entrypoint=entrypoint):
                self.assertLess(
                    text.index("release_integrity.py verify"), text.index("mkdir -p")
                )
                self.assertLess(
                    text.index("release_integrity.py startup-compatibility"),
                    text.index("mkdir -p"),
                )

    def test_dockerfile_bakes_manifest_after_source_copy(self):
        text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertLess(text.index("COPY . ."), text.index("release_integrity.py manifest"))

class RunningReleaseVerificationTests(unittest.TestCase):
    def test_actual_container_image_revision_and_sentinels_are_compared(self):
        expected_image = "example.invalid/pdfsearch@sha256:" + "a" * 64
        expected_revision = "b" * 40
        image_id = "sha256:" + "c" * 64
        expected_manifest = release_integrity.build_manifest(ROOT)
        responses = iter(
            [
                subprocess.CompletedProcess(
                    [],
                    0,
                    json.dumps([{"Config": {"Image": expected_image}, "Image": image_id}]),
                    "",
                ),
                subprocess.CompletedProcess(
                    [],
                    0,
                    json.dumps(
                        [
                            {
                                "Id": image_id,
                                "Config": {
                                    "Labels": {
                                        "org.opencontainers.image.revision": expected_revision
                                    }
                                },
                                "RepoDigests": [expected_image],
                            }
                        ]
                    ),
                    "",
                ),
                subprocess.CompletedProcess([], 0, "release_integrity_ok\n", ""),
                subprocess.CompletedProcess([], 0, json.dumps(expected_manifest), ""),
            ]
        )

        evidence = verify_running_release.verify_running_release(
            container="pdfsearch-web-1",
            expected_image=expected_image,
            expected_revision=expected_revision,
            checkout_root=ROOT,
            run=lambda _command: next(responses),
        )

        self.assertEqual(evidence["config_image"], expected_image)
        self.assertEqual(evidence["image_id"], image_id)
        self.assertEqual(evidence["oci_revision"], expected_revision)
        self.assertEqual(evidence["status"], "verified")

    def test_mutable_config_image_is_rejected_even_if_image_id_exists(self):
        expected_image = "example.invalid/pdfsearch@sha256:" + "a" * 64
        image_id = "sha256:" + "c" * 64
        responses = iter(
            [
                subprocess.CompletedProcess(
                    [],
                    0,
                    json.dumps(
                        [
                            {
                                "Config": {"Image": "example.invalid/pdfsearch:latest"},
                                "Image": image_id,
                            }
                        ]
                    ),
                    "",
                ),
                subprocess.CompletedProcess(
                    [],
                    0,
                    json.dumps(
                        [
                            {
                                "Id": image_id,
                                "Config": {
                                    "Labels": {
                                        "org.opencontainers.image.revision": "b" * 40
                                    }
                                },
                                "RepoDigests": [expected_image],
                            }
                        ]
                    ),
                    "",
                ),
            ]
        )
        with self.assertRaisesRegex(
            release_integrity.IntegrityError, "image reference drift"
        ):
            verify_running_release.verify_running_release(
                container="pdfsearch-web-1",
                expected_image=expected_image,
                expected_revision="b" * 40,
                checkout_root=ROOT,
                run=lambda _command: next(responses),
            )

    def test_dry_run_does_not_invoke_docker(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            result = verify_running_release.main(
                [
                    "--container",
                    "pdfsearch-web-1",
                    "--expected-image",
                    "example.invalid/pdfsearch@sha256:" + "a" * 64,
                    "--expected-revision",
                    "b" * 40,
                    "--dry-run",
                ]
            )
        self.assertEqual(result, 0)
        self.assertIn("<actual-container-.Image>", stdout.getvalue())
        self.assertIn("dry_run_only", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
