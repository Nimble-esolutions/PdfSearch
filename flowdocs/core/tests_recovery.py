import hashlib
import io
import json
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.hashers import make_password
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from core.emergency_recovery import (
    MANIFEST,
    RecoverySetError,
    apply_prune,
    create_set,
    list_sets,
    plan_prune,
    prepare_set,
    validate_workspace,
    verify_set,
)


def _database(path: Path, *, migration: tuple[str, str], recovery_user=False):
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
        connection.execute(
            "CREATE TABLE child (id INTEGER PRIMARY KEY, parent_id INTEGER "
            "REFERENCES parent(id))"
        )
        connection.execute("INSERT INTO parent VALUES (1)")
        connection.execute("INSERT INTO child VALUES (1, 1)")
        connection.execute(
            "CREATE TABLE django_migrations ("
            "id INTEGER PRIMARY KEY, app TEXT, name TEXT)"
        )
        connection.execute(
            "INSERT INTO django_migrations (app, name) VALUES (?, ?)",
            migration,
        )
        if recovery_user:
            connection.execute(
                "CREATE TABLE core_customuser ("
                "id INTEGER PRIMARY KEY, username TEXT, password TEXT, "
                "is_active INTEGER, is_superuser INTEGER, role TEXT)"
            )
            connection.execute(
                "INSERT INTO core_customuser VALUES (1, ?, ?, 1, 1, ?)",
                (
                    "recovery",
                    make_password("recovery-password"),
                    "superadmin",
                ),
            )
        connection.commit()
    finally:
        connection.close()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class EmergencyRecoveryTests(SimpleTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data = self.root / "data"
        self.control = self.root / "control"
        self.recovery = self.root / "recovery"
        self.data.mkdir()
        self.control.mkdir()
        self.application_db = self.data / "db.sqlite3"
        self.control_db = self.control / "control.sqlite3"
        _database(
            self.application_db,
            migration=("core", "0020"),
            recovery_user=True,
        )
        _database(
            self.control_db,
            migration=("vaultops", "0007"),
        )
        self.identity = SimpleNamespace(
            deployment_id="test-deployment",
            dataset_id="test-dataset",
            app_image_digest="sha256:test-image",
            app_release_version="release-test",
        )
        self.settings = override_settings(
            DATA_ROOT=self.data,
            BACKUP_DIR=self.root / "backups",
            RECOVERY_SET_ROOT=self.recovery,
            MEDIA_ROOT=self.data / "media",
            DATABASES={
                "default": {
                    "ENGINE": "django.db.backends.sqlite3",
                    "NAME": str(self.application_db),
                },
                "control": {
                    "ENGINE": "django.db.backends.sqlite3",
                    "NAME": str(self.control_db),
                },
            },
            RUNTIME_GENERATION_ID="runtime-a",
            RUNTIME_MANIFEST_DIGEST="digest-a",
            ENV_IDENTITY=self.identity,
            ACTIVATION_RECOVERY_SUPERADMIN_USERNAME="recovery",
            ACTIVATION_RECOVERY_SUPERADMIN_PASSWORD="recovery-password",
        )
        self.settings.enable()
        self.migrations = patch(
            "core.emergency_recovery.migration_leaves",
            return_value={"default": ["core.0020"], "control": ["vaultops.0007"]},
        )
        self.migrations.start()
        self.required_migrations = patch(
            "core.emergency_recovery.required_migrations",
            return_value={"default": ["core.0020"], "control": ["vaultops.0007"]},
        )
        self.required_migrations.start()

    def tearDown(self):
        self.required_migrations.stop()
        self.migrations.stop()
        self.settings.disable()
        self.temporary.cleanup()

    def test_create_is_atomic_verified_and_reused_for_same_identity(self):
        first = create_set("manual")
        second = create_set("pre-migration")

        self.assertFalse(first["reused"])
        self.assertTrue(second["reused"])
        self.assertEqual(first["set_id"], second["set_id"])
        self.assertEqual(verify_set(first["set_id"])["verification_state"], "verified")
        self.assertEqual(len(list_sets()), 1)
        self.assertEqual(
            set(first["databases"]), {"application", "control"}
        )
        self.assertFalse(list(self.recovery.glob(".*-")))

    def test_concurrent_creators_publish_one_matching_set(self):
        with ThreadPoolExecutor(max_workers=2) as executor:
            records = list(executor.map(lambda _: create_set("manual"), range(2)))
        self.assertEqual(len({item["set_id"] for item in records}), 1)
        self.assertEqual(len(list_sets()), 1)

    def test_failed_second_database_never_publishes_partial_set(self):
        from core import emergency_recovery

        original = emergency_recovery._backup_database

        def fail_control(source, destination):
            if destination.name == "control.sqlite3":
                raise RecoverySetError("disk_full")
            return original(source, destination)

        with patch(
            "core.emergency_recovery._backup_database", side_effect=fail_control
        ):
            with self.assertRaisesRegex(RecoverySetError, "disk_full"):
                create_set("pre-migration")
        self.assertEqual(list_sets(), [])
        self.assertEqual(
            [path for path in self.recovery.iterdir() if path.name != ".create.lock"],
            [],
        )

    def test_prune_requires_current_read_only_plan(self):
        records = []
        for index in range(5):
            with patch(
                "core.emergency_recovery._data_identity",
                return_value={"revision": index},
            ):
                records.append(create_set("manual"))
        for record in records:
            manifest_path = self.recovery / record["set_id"] / MANIFEST
            manifest = json.loads(manifest_path.read_text())
            manifest["created_at"] = "2020-01-01T00:00:00+00:00"
            manifest_path.write_text(json.dumps(manifest))

        plan = plan_prune()
        self.assertEqual(len(plan["candidates"]), 2)
        with self.assertRaisesRegex(RecoverySetError, "stale_prune_plan"):
            apply_prune("wrong")
        applied = apply_prune(plan["plan_id"])
        self.assertEqual(len(applied["removed"]), 2)
        self.assertEqual(len(list_sets()), 3)

    def test_prepare_rejects_existing_target_and_validates_isolated_copy(self):
        recovery_set = create_set("manual")
        target = self.root.parent / f"prepared-{recovery_set['set_id']}"
        if target.exists():
            self.fail(f"unexpected test target exists: {target}")
        try:
            result = prepare_set(recovery_set["set_id"], target)
            self.assertEqual(result["verification_state"], "blocked")
            self.assertEqual(
                result["database_verification_state"], "verified"
            )
            self.assertTrue(result["database_verified"])
            self.assertFalse(result["recovery_ready"])
            self.assertEqual(
                [item["code"] for item in result["blockers"]],
                ["index_rebuild_required"],
            )
            self.assertTrue(result["database_only"])
            with self.assertRaisesRegex(RecoverySetError, "target_exists"):
                prepare_set(recovery_set["set_id"], target)
        finally:
            if target.exists():
                import shutil

                shutil.rmtree(target)

    def test_validate_separates_database_verification_from_recovery_readiness(self):
        recovery_set = create_set("manual")
        target = self.root.parent / f"prepared-{recovery_set['set_id']}"
        try:
            result = prepare_set(recovery_set["set_id"], target)

            self.assertEqual(result["verification_state"], "blocked")
            self.assertEqual(
                result["database_verification_state"], "verified"
            )
            self.assertTrue(result["database_verified"])
            self.assertFalse(result["recovery_ready"])
            self.assertEqual(
                result["recovery_authentication"], {"state": "verified"}
            )
            self.assertEqual(
                result["migrations"]["default"]["state"], "compatible"
            )
            self.assertEqual(
                result["migrations"]["control"]["state"], "compatible"
            )
            self.assertEqual(
                result["identity_compatibility"]["dataset_id"]["state"],
                "compatible",
            )
        finally:
            if target.exists():
                import shutil

                shutil.rmtree(target)

    def test_validate_never_mutates_prepared_databases(self):
        recovery_set = create_set("manual")
        target = self.root.parent / f"prepared-{recovery_set['set_id']}"
        try:
            prepare_set(recovery_set["set_id"], target)
            paths = [
                target / "application.sqlite3",
                target / "control.sqlite3",
            ]
            before = {
                path.name: (_sha256(path), path.stat().st_mtime_ns)
                for path in paths
            }

            validate_workspace(target)

            after = {
                path.name: (_sha256(path), path.stat().st_mtime_ns)
                for path in paths
            }
            self.assertEqual(after, before)
        finally:
            if target.exists():
                import shutil

                shutil.rmtree(target)

    def test_validate_reports_secret_free_identity_and_migration_blockers(self):
        recovery_set = create_set("manual")
        target = self.root.parent / f"prepared-{recovery_set['set_id']}"
        try:
            prepare_set(recovery_set["set_id"], target)
            manifest_path = target / MANIFEST
            manifest = json.loads(manifest_path.read_text())
            manifest["identity"]["deployment_id"] = "other-deployment"
            manifest["identity"]["dataset_id"] = "other-dataset"
            manifest["identity"]["image_digest"] = "sha256:other-image"
            manifest["migration_leaves"]["control"] = ["vaultops.9999"]
            manifest_path.write_text(json.dumps(manifest))

            result = validate_workspace(target)

            codes = {item["code"] for item in result["blockers"]}
            self.assertIn("deployment_id_incompatible", codes)
            self.assertIn("dataset_id_incompatible", codes)
            self.assertIn("image_digest_incompatible", codes)
            self.assertIn("migration_evidence_incompatible", codes)
            serialized = json.dumps(result)
            self.assertNotIn("other-deployment", serialized)
            self.assertNotIn("other-dataset", serialized)
            self.assertNotIn("sha256:other-image", serialized)
            self.assertNotIn("test-dataset", serialized)
            self.assertNotIn("sha256:test-image", serialized)
        finally:
            if target.exists():
                import shutil

                shutil.rmtree(target)

    def test_validate_reports_required_migration_missing_from_database(self):
        recovery_set = create_set("manual")
        target = self.root.parent / f"prepared-{recovery_set['set_id']}"
        try:
            prepare_set(recovery_set["set_id"], target)
            application = target / "application.sqlite3"
            connection = sqlite3.connect(application)
            connection.execute(
                "DELETE FROM django_migrations WHERE app='core' AND name='0020'"
            )
            connection.commit()
            connection.close()
            manifest_path = target / MANIFEST
            manifest = json.loads(manifest_path.read_text())
            manifest["databases"]["application"]["sha256"] = _sha256(application)
            manifest_path.write_text(json.dumps(manifest))

            result = validate_workspace(target)

            blockers = [
                item
                for item in result["blockers"]
                if item["code"] == "required_migrations_unapplied"
            ]
            self.assertEqual(
                blockers,
                [
                    {
                        "code": "required_migrations_unapplied",
                        "scope": "default",
                        "count": 1,
                    }
                ],
            )
        finally:
            if target.exists():
                import shutil

                shutil.rmtree(target)

    def test_validate_rejects_unknown_application_and_control_migrations(self):
        recovery_set = create_set("manual")
        target = self.root.parent / f"prepared-{recovery_set['set_id']}"
        try:
            prepare_set(recovery_set["set_id"], target)
            database_cases = (
                ("application", "core", "9999_future"),
                ("control", "vaultops", "9999_future"),
            )
            manifest_path = target / MANIFEST
            manifest = json.loads(manifest_path.read_text())
            for database_key, app, migration in database_cases:
                database = target / manifest["databases"][database_key]["file"]
                connection = sqlite3.connect(database)
                connection.execute(
                    "INSERT INTO django_migrations (app, name) VALUES (?, ?)",
                    (app, migration),
                )
                connection.commit()
                connection.close()
                manifest["databases"][database_key]["sha256"] = _sha256(database)
            manifest_path.write_text(json.dumps(manifest))

            result = validate_workspace(target)

            blockers = [
                item
                for item in result["blockers"]
                if item["code"] == "unknown_applied_migrations"
            ]
            self.assertEqual(
                blockers,
                [
                    {
                        "code": "unknown_applied_migrations",
                        "scope": "default",
                        "count": 1,
                    },
                    {
                        "code": "unknown_applied_migrations",
                        "scope": "control",
                        "count": 1,
                    },
                ],
            )
            self.assertEqual(
                result["migrations"]["default"][
                    "unknown_applied_migration_count"
                ],
                1,
            )
            self.assertEqual(
                result["migrations"]["control"][
                    "unknown_applied_migration_count"
                ],
                1,
            )
            self.assertNotIn("9999_future", json.dumps(result))
        finally:
            if target.exists():
                import shutil

                shutil.rmtree(target)

    def test_validate_reports_authentication_and_media_as_typed_blockers(self):
        connection = sqlite3.connect(self.application_db)
        connection.execute(
            "CREATE TABLE core_pdffile (id INTEGER PRIMARY KEY, file TEXT)"
        )
        connection.execute(
            "INSERT INTO core_pdffile VALUES (1, 'private-document-name.pdf')"
        )
        connection.commit()
        connection.close()
        recovery_set = create_set("manual")
        target = self.root.parent / f"prepared-{recovery_set['set_id']}"
        try:
            prepare_set(recovery_set["set_id"], target)
            with override_settings(
                ACTIVATION_RECOVERY_SUPERADMIN_PASSWORD="wrong-password"
            ):
                result = validate_workspace(target)

            codes = {item["code"] for item in result["blockers"]}
            self.assertIn("recovery_superadmin_unproven", codes)
            self.assertIn("referenced_media_missing", codes)
            self.assertEqual(result["referenced_media"]["missing_count"], 1)
            self.assertNotIn("private-document-name.pdf", json.dumps(result))
        finally:
            if target.exists():
                import shutil

                shutil.rmtree(target)

    def test_validate_command_exits_nonzero_and_preserves_workspace(self):
        recovery_set = create_set("manual")
        target = self.root.parent / f"prepared-{recovery_set['set_id']}"
        try:
            prepare_set(recovery_set["set_id"], target)
            output = io.StringIO()

            with self.assertRaisesMessage(
                CommandError, "workspace_not_recovery_ready"
            ):
                call_command(
                    "emergency_db",
                    "validate",
                    "--workspace",
                    str(target),
                    stdout=output,
                )

            report = json.loads(output.getvalue())
            self.assertEqual(report["verification_state"], "blocked")
            self.assertEqual(
                report["database_verification_state"], "verified"
            )
            self.assertFalse(report["recovery_ready"])
            self.assertTrue((target / "application.sqlite3").is_file())
            self.assertTrue((target / "control.sqlite3").is_file())
            self.assertTrue((target / MANIFEST).is_file())
        finally:
            if target.exists():
                import shutil

                shutil.rmtree(target)

    def test_validate_rejects_workspace_and_database_symlinks(self):
        recovery_set = create_set("manual")
        target = self.root.parent / f"prepared-{recovery_set['set_id']}"
        workspace_link = self.root.parent / f"prepared-link-{recovery_set['set_id']}"
        try:
            prepare_set(recovery_set["set_id"], target)
            workspace_link.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(
                RecoverySetError, "workspace_not_found"
            ):
                validate_workspace(workspace_link)

            database = target / "application.sqlite3"
            database_copy = target / "application-copy.sqlite3"
            database.rename(database_copy)
            database.symlink_to(database_copy.name)
            with self.assertRaisesRegex(
                RecoverySetError, "workspace_database_path_unsafe"
            ):
                validate_workspace(target)
        finally:
            if workspace_link.is_symlink():
                workspace_link.unlink()
            if target.exists():
                import shutil

                shutil.rmtree(target)

    def test_validate_rejects_manifest_database_path_escape(self):
        recovery_set = create_set("manual")
        target = self.root.parent / f"prepared-{recovery_set['set_id']}"
        try:
            prepare_set(recovery_set["set_id"], target)
            manifest_path = target / MANIFEST
            manifest = json.loads(manifest_path.read_text())
            manifest["databases"]["application"]["file"] = "../db.sqlite3"
            manifest_path.write_text(json.dumps(manifest))

            with self.assertRaisesRegex(
                RecoverySetError, "workspace_database_path_unsafe"
            ):
                validate_workspace(target)
        finally:
            if target.exists():
                import shutil

                shutil.rmtree(target)
