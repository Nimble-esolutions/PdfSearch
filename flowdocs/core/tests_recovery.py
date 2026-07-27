import json
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from core.emergency_recovery import (
    MANIFEST,
    RecoverySetError,
    apply_prune,
    create_set,
    list_sets,
    plan_prune,
    prepare_set,
    verify_set,
)


def _database(path: Path):
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
        connection.commit()
    finally:
        connection.close()


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
        _database(self.application_db)
        _database(self.control_db)
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
        )
        self.settings.enable()
        self.migrations = patch(
            "core.emergency_recovery.migration_leaves",
            return_value={"default": ["core.0020"], "control": ["vaultops.0007"]},
        )
        self.migrations.start()

    def tearDown(self):
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
            self.assertEqual(result["verification_state"], "verified")
            self.assertTrue(result["database_only"])
            with self.assertRaisesRegex(RecoverySetError, "target_exists"):
                prepare_set(recovery_set["set_id"], target)
        finally:
            if target.exists():
                import shutil

                shutil.rmtree(target)
