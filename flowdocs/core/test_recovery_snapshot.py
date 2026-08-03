"""Stable, product-neutral recovery snapshot tests."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import uuid
from pathlib import Path
from unittest import TestCase

from core.recovery_snapshot import (
    SnapshotCaptureError,
    capture_consistency_snapshot,
)


class RecoverySnapshotTests(TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data = self.root / "data"
        self.media = self.data / "media"
        self.media.mkdir(parents=True)
        self.database = self.data / "db.sqlite3"
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "CREATE TABLE core_pdffile "
                "(indexed INTEGER, processing_status TEXT)"
            )
            connection.execute("CREATE TABLE core_folder (id INTEGER PRIMARY KEY)")
            connection.execute(
                "CREATE TABLE core_customuser (id INTEGER PRIMARY KEY)"
            )
            connection.execute(
                "CREATE TABLE django_migrations "
                "(app TEXT, name TEXT, applied TEXT)"
            )
            connection.execute(
                "INSERT INTO django_migrations VALUES "
                "('core', '0029', '2026-08-03T00:00:00')"
            )
            connection.executemany(
                "INSERT INTO core_pdffile VALUES (?, ?)",
                [(1, "ready"), (0, "queued")],
            )
            connection.executemany(
                "INSERT INTO core_customuser VALUES (?)",
                [(1,), (2,), (3,)],
            )
        (self.media / "one.pdf").write_bytes(b"pdf-one")
        self.snapshots = self.root / "snapshots"

    def tearDown(self):
        self.temporary.cleanup()

    def capture(self, **overrides):
        values = {
            "snapshot_id": str(uuid.uuid4()),
            "source_roots": {"media": self.media},
            "database_path": self.database,
            "workspace_root": self.snapshots,
            "configuration": {"release": "2026.08.03"},
        }
        values.update(overrides)
        return capture_consistency_snapshot(**values)

    def test_captures_database_and_files_with_bound_evidence(self):
        snapshot = self.capture()
        workspace = Path(snapshot.workspace_path)
        evidence = json.loads(
            (workspace / "snapshot-evidence.json").read_text(encoding="utf-8")
        )
        self.assertTrue(evidence["source_stable"])
        self.assertEqual(evidence["consistency"]["sqlite_integrity"], "ok")
        self.assertEqual(evidence["inventory"]["counts"]["pdf_rows"], 2)
        self.assertEqual(evidence["inventory"]["counts"]["users"], 3)
        self.assertEqual(evidence["inventory"]["database"]["migrations"]["latest"], "core.0029")
        self.assertTrue((workspace / "media" / "one.pdf").is_file())

    def test_falls_back_to_django_default_user_table(self):
        with sqlite3.connect(self.database) as connection:
            connection.execute("DROP TABLE core_customuser")
            connection.execute("CREATE TABLE auth_user (id INTEGER PRIMARY KEY)")
            connection.executemany(
                "INSERT INTO auth_user VALUES (?)",
                [(1,), (2,)],
            )

        snapshot = self.capture()
        evidence = json.loads(
            (
                Path(snapshot.workspace_path) / "snapshot-evidence.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(evidence["inventory"]["counts"]["users"], 2)

    def test_one_mutation_retries_and_converges(self):
        snapshot_id = str(uuid.uuid4())
        callbacks = []

        def mutate_once(stage):
            callbacks.append(stage)
            if stage == "copy_complete" and callbacks.count(stage) == 1:
                (self.media / "one.pdf").write_bytes(b"pdf-two")

        snapshot = self.capture(
            snapshot_id=snapshot_id,
            progress_callback=mutate_once,
        )
        evidence = json.loads(
            (Path(snapshot.workspace_path) / "snapshot-evidence.json").read_text()
        )
        self.assertEqual(evidence["attempt"], 2)
        self.assertEqual(
            (Path(snapshot.workspace_path) / "media" / "one.pdf").read_bytes(),
            b"pdf-two",
        )

    def test_persistent_mutation_never_publishes_a_snapshot(self):
        snapshot_id = str(uuid.uuid4())

        def mutate(stage):
            if stage == "copy_complete":
                with (self.media / "one.pdf").open("ab") as stream:
                    stream.write(b"x")

        with self.assertRaisesRegex(SnapshotCaptureError, "snapshot_source_mutated"):
            self.capture(
                snapshot_id=snapshot_id,
                progress_callback=mutate,
                max_attempts=2,
            )
        self.assertFalse((self.snapshots / f"snapshot-{snapshot_id}").exists())
        self.assertFalse(any(self.snapshots.glob(".snapshot-*-attempt-*")))

    def test_tampered_final_snapshot_is_not_resumed(self):
        snapshot_id = str(uuid.uuid4())
        snapshot = self.capture(snapshot_id=snapshot_id)
        target = Path(snapshot.workspace_path) / "media" / "one.pdf"
        target.chmod(0o600)
        target.write_bytes(b"tampered")
        with self.assertRaisesRegex(SnapshotCaptureError, "snapshot_resume_invalid"):
            self.capture(snapshot_id=snapshot_id)

    def test_foreign_key_violation_fails_before_finalization(self):
        with sqlite3.connect(self.database) as connection:
            connection.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
            connection.execute(
                "CREATE TABLE child "
                "(parent_id INTEGER REFERENCES parent(id))"
            )
            connection.execute("INSERT INTO child VALUES (99)")
        with self.assertRaisesRegex(
            SnapshotCaptureError,
            "snapshot_database_foreign_keys_failed",
        ):
            self.capture()
