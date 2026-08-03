import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings

from dataops.job_scheduler import ScheduleConfigurationError, parse_schedule, queue_due_backup_jobs, schedule_matches
from dataops.models import BackupJob, DataConnection, DataOperation
from dataops.worker import queue_backup_if_due


class BackupJobSchedulerTests(TestCase):
    databases = {"default", "control"}

    def test_cron_matching_supports_timezone_steps_and_sunday_alias(self):
        moment = datetime(2026, 8, 2, 1, 30, tzinfo=timezone.utc)  # Sunday, 07:00 India
        self.assertTrue(schedule_matches("*/15 7 * * 7", moment, "Asia/Kolkata"))
        self.assertFalse(schedule_matches("*/15 8 * * 0", moment, "Asia/Kolkata"))

    def test_invalid_cron_is_rejected(self):
        with self.assertRaises(ScheduleConfigurationError):
            parse_schedule("61 2 * * *")
        with self.assertRaises(ScheduleConfigurationError):
            parse_schedule("0 2 * *")

    @override_settings(MAINTENANCE_SCHEDULER_ENABLED=True)
    def test_due_legacy_profile_job_fails_closed_in_v3(self):
        job = BackupJob.objects.using("control").create(
            name="Nightly archive",
            slug="nightly-archive",
            source_profile_key="source",
            target_profile_key="target",
            schedule="30 1 * * *",
            timezone="UTC",
        )
        moment = datetime(2026, 8, 2, 1, 30, tzinfo=timezone.utc)
        self.assertEqual(queue_due_backup_jobs(moment=moment), 0)
        self.assertFalse(DataOperation.objects.using("control").exists())
        job.refresh_from_db(using="control")
        self.assertEqual(
            job.last_run_status,
            "manual_required:v3_only",
        )

    @override_settings(MAINTENANCE_SCHEDULER_ENABLED=False)
    def test_disabled_scheduler_does_not_evaluate_legacy_jobs(self):
        job = BackupJob.objects.using("control").create(
            name="Nightly archive",
            slug="nightly-disabled",
            source_profile_key="source",
            target_profile_key="target",
            schedule="30 1 * * *",
            timezone="UTC",
        )
        moment = datetime(2026, 8, 2, 1, 30, tzinfo=timezone.utc)
        self.assertEqual(queue_due_backup_jobs(moment=moment), 0)
        job.refresh_from_db(using="control")
        self.assertEqual(job.last_run_status, "")

    def test_v3_scheduled_policy_queues_lifecycle_planned_backup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_root = root / "data"
            control_root = root / "control"
            data_root.mkdir()
            control_root.mkdir()
            identity = SimpleNamespace(
                app_env=SimpleNamespace(value="production"),
                deployment_id="prod-2026",
                dataset_id="ai-sahakar-prod-2026",
            )
            with override_settings(
                DATAOPS_ENABLED=True,
                MAINTENANCE_SCHEDULER_ENABLED=True,
                APP_ENV="production",
                DEPLOYMENT_ID="prod-2026",
                DATASET_ID="ai-sahakar-prod-2026",
                DATA_ROOT=data_root,
                DATA_CONTROL_ROOT=control_root,
                ENV_IDENTITY=identity,
            ):
                connection = DataConnection.objects.using("control").create(
                    name="Primary RustFS",
                    provider="rustfs",
                    endpoint="https://rustfs.example.invalid",
                    bucket="prod-recovery",
                    dataset_id="ai-sahakar-prod-2026",
                    credential_ref="secret://dataops/prod",
                    is_primary=True,
                    capabilities={
                        "probed": True,
                        "read": True,
                        "write": True,
                        "conditional_write": True,
                    },
                )
                operation = queue_backup_if_due(trigger="scheduler")

        self.assertIsNotNone(operation)
        self.assertEqual(operation.kind, DataOperation.Kind.BACKUP)
        self.assertEqual(operation.connection, connection)
        self.assertEqual(operation.lifecycle_plan["contract_version"], 3)
        self.assertEqual(operation.lifecycle_route, "backup")
        self.assertEqual(operation.profile_key, "")
        self.assertEqual(operation.source_profile_key, "")
        self.assertEqual(operation.destination_profile_key, "")

    @override_settings(
        DATAOPS_ENABLED=True,
        MAINTENANCE_SCHEDULER_ENABLED=False,
    )
    def test_disabled_scheduler_blocks_policy_backup_queue(self):
        with patch("dataops.v3_planning.runtime_config") as config:
            self.assertIsNone(queue_backup_if_due(trigger="scheduler"))
        config.assert_not_called()

    @override_settings(
        DATAOPS_ENABLED=True,
        MAINTENANCE_SCHEDULER_ENABLED=False,
    )
    def test_maintenance_cycle_reconciles_without_automatic_queueing(self):
        from core.management.commands.run_maintenance_jobs import _run_dataops_cycle

        with (
            patch("dataops.worker.queue_backup_if_due") as policy_queue,
            patch("dataops.job_scheduler.queue_due_backup_jobs") as legacy_queue,
            patch("dataops.worker.reconcile_receipts") as reconcile,
            patch("dataops.quarantine.cleanup_mirror_quarantines") as cleanup,
        ):
            _run_dataops_cycle()

        policy_queue.assert_not_called()
        legacy_queue.assert_not_called()
        reconcile.assert_called_once_with(limit=10)
        cleanup.assert_called_once_with()
