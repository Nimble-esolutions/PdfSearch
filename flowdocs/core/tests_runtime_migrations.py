import io
import os
import tempfile
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.db import connection, migrations, models
from django.db.migrations.loader import MigrationLoader
from django.test import SimpleTestCase, TestCase

from core.runtime_migrations import (
    RuntimeMigrationDecision,
    classify_operation,
)


class RuntimeMigrationClassificationTests(TestCase):
    def _state_before_upload_batch(self):
        loader = MigrationLoader(connection)
        migration = loader.get_migration(
            "core", "0028_uploadbatch_uploadbatchitem"
        )
        return migration, loader.project_state(migration.dependencies)

    def test_current_upload_batch_migration_is_additive(self):
        migration, state = self._state_before_upload_batch()
        decisions = []
        for operation in migration.operations:
            old_state = state.clone()
            operation.state_forwards(migration.app_label, state)
            decisions.append(
                classify_operation(
                    app_label=migration.app_label,
                    migration_name=migration.name,
                    operation=operation,
                    old_state=old_state,
                    new_state=state,
                    connection=connection,
                )
            )

        self.assertTrue(all(item.safe for item in decisions))
        self.assertEqual(
            [item.reason for item in decisions],
            ["state_only_field_metadata", "additive_schema", "additive_schema"],
        )

    def test_database_changing_alter_field_requires_isolated_candidate(self):
        migration, state = self._state_before_upload_batch()
        operation = migrations.AlterField(
            model_name="pdffile",
            name="lifecycle",
            field=models.CharField(max_length=31, default="uploaded"),
        )
        old_state = state.clone()
        operation.state_forwards(migration.app_label, state)

        decision = classify_operation(
            app_label=migration.app_label,
            migration_name="unsafe_alter",
            operation=operation,
            old_state=old_state,
            new_state=state,
            connection=connection,
        )

        self.assertFalse(decision.safe)
        self.assertEqual(decision.reason, "isolated_candidate_required")

    def test_data_and_destructive_operations_fail_closed(self):
        migration, state = self._state_before_upload_batch()
        for operation in (
            migrations.DeleteModel("PDFFile"),
            migrations.RunPython(migrations.RunPython.noop),
            migrations.RunSQL("SELECT 1"),
            migrations.AddField(
                "pdffile", "unsafe_field", models.TextField(null=True)
            ),
        ):
            decision = classify_operation(
                app_label=migration.app_label,
                migration_name="unsafe_operation",
                operation=operation,
                old_state=state,
                new_state=state,
                connection=connection,
            )
            self.assertFalse(decision.safe, operation.__class__.__name__)


class SafeRuntimeMigrationCommandTests(SimpleTestCase):
    safe_decision = RuntimeMigrationDecision(
        "core.0028_uploadbatch_uploadbatchitem",
        "CreateModel",
        True,
        "additive_schema",
    )
    unsafe_decision = RuntimeMigrationDecision(
        "core.0029_unsafe",
        "RunPython",
        False,
        "isolated_candidate_required",
    )

    def test_safe_plan_creates_verified_recovery_then_applies(self):
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as control_root, patch.dict(
            os.environ, {"DATA_CONTROL_ROOT": control_root}
        ), patch(
            "core.management.commands.apply_safe_runtime_migrations."
            "runtime_migration_plan",
            side_effect=[[self.safe_decision], []],
        ), patch(
            "core.management.commands.apply_safe_runtime_migrations.create_set",
            return_value={
                "set_id": "rs-test",
                "reused": False,
                "verification_state": "verified",
            },
        ) as create_set, patch(
            "core.management.commands.apply_safe_runtime_migrations.call_command"
        ) as migrate:
            call_command("apply_safe_runtime_migrations", stdout=output)

        create_set.assert_called_once_with("pre-migration")
        migrate.assert_called_once()
        self.assertIn("runtime_migration_status=applied_and_verified", output.getvalue())

    def test_unsafe_plan_fails_before_recovery_or_migration(self):
        with tempfile.TemporaryDirectory() as control_root, patch.dict(
            os.environ, {"DATA_CONTROL_ROOT": control_root}
        ), patch(
            "core.management.commands.apply_safe_runtime_migrations."
            "runtime_migration_plan",
            return_value=[self.unsafe_decision],
        ), patch(
            "core.management.commands.apply_safe_runtime_migrations.create_set"
        ) as create_set, patch(
            "core.management.commands.apply_safe_runtime_migrations.call_command"
        ) as migrate, self.assertRaisesMessage(
            CommandError, "runtime_migration_requires_isolated_candidate"
        ):
            call_command("apply_safe_runtime_migrations")

        create_set.assert_not_called()
        migrate.assert_not_called()
    def test_check_mode_never_mutates(self):
        with tempfile.TemporaryDirectory() as control_root, patch.dict(
            os.environ, {"DATA_CONTROL_ROOT": control_root}
        ), patch(
            "core.management.commands.apply_safe_runtime_migrations."
            "runtime_migration_plan",
            return_value=[self.safe_decision],
        ), patch(
            "core.management.commands.apply_safe_runtime_migrations.create_set"
        ) as create_set, patch(
            "core.management.commands.apply_safe_runtime_migrations.call_command"
        ) as migrate:
            call_command("apply_safe_runtime_migrations", check=True)

        create_set.assert_not_called()
        migrate.assert_not_called()
