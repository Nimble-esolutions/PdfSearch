from datetime import datetime, timezone

from django.test import TestCase

from dataops.job_scheduler import ScheduleConfigurationError, parse_schedule, queue_due_backup_jobs, schedule_matches
from dataops.models import BackupJob, DataOperation


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

    def test_due_job_is_queued_once_per_utc_minute(self):
        BackupJob.objects.using("control").create(
            name="Nightly archive",
            slug="nightly-archive",
            source_profile_key="source",
            target_profile_key="target",
            schedule="30 1 * * *",
            timezone="UTC",
        )
        moment = datetime(2026, 8, 2, 1, 30, tzinfo=timezone.utc)
        self.assertEqual(queue_due_backup_jobs(moment=moment), 1)
        self.assertEqual(queue_due_backup_jobs(moment=moment), 0)
        operation = DataOperation.objects.using("control").get(kind=DataOperation.Kind.SYNC)
        self.assertEqual(operation.checkpoint, {"job_slug": "nightly-archive", "trigger": "schedule"})
