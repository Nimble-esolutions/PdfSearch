"""Small dependency-free cron scheduler for S3 backup jobs."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import transaction

from .models import BackupJob, DataOperation, DataOpsAuditEvent


class ScheduleConfigurationError(ValueError):
    pass


def _field(value: str, minimum: int, maximum: int) -> tuple[set[int], bool]:
    values: set[int] = set()
    wildcard = value in {"*", "*/1"}
    for item in value.split(","):
        item = item.strip()
        if not item:
            raise ScheduleConfigurationError("cron_field_empty")
        base, separator, step_text = item.partition("/")
        try:
            step = int(step_text) if separator else 1
        except ValueError as exc:
            raise ScheduleConfigurationError("cron_step_invalid") from exc
        if step < 1:
            raise ScheduleConfigurationError("cron_step_invalid")
        if base == "*":
            start, end = minimum, maximum
        elif "-" in base:
            start_text, end_text = base.split("-", 1)
            try:
                start, end = int(start_text), int(end_text)
            except ValueError as exc:
                raise ScheduleConfigurationError("cron_range_invalid") from exc
        else:
            try:
                start = end = int(base)
            except ValueError as exc:
                raise ScheduleConfigurationError("cron_value_invalid") from exc
        if start < minimum or end > maximum or start > end:
            raise ScheduleConfigurationError("cron_value_out_of_range")
        values.update(range(start, end + 1, step))
    return values, wildcard


def parse_schedule(expression: str):
    parts = str(expression or "").split()
    if len(parts) != 5:
        raise ScheduleConfigurationError("cron_requires_five_fields")
    minute, _ = _field(parts[0], 0, 59)
    hour, _ = _field(parts[1], 0, 23)
    day, day_wildcard = _field(parts[2], 1, 31)
    month, _ = _field(parts[3], 1, 12)
    weekday, weekday_wildcard = _field(parts[4], 0, 7)
    if 7 in weekday:
        weekday.add(0)
        weekday.discard(7)
    return minute, hour, day, month, weekday, day_wildcard, weekday_wildcard


def schedule_matches(expression: str, moment: datetime, timezone_name: str = "UTC") -> bool:
    try:
        local = moment.astimezone(ZoneInfo(timezone_name))
    except ZoneInfoNotFoundError as exc:
        raise ScheduleConfigurationError("timezone_invalid") from exc
    minute, hour, day, month, weekday, day_wildcard, weekday_wildcard = parse_schedule(expression)
    cron_weekday = (local.weekday() + 1) % 7
    day_matches = local.day in day
    weekday_matches = cron_weekday in weekday
    if not day_wildcard and not weekday_wildcard:
        calendar_match = day_matches or weekday_matches
    else:
        calendar_match = day_matches and weekday_matches
    return local.minute in minute and local.hour in hour and local.month in month and calendar_match


def queue_due_backup_jobs(*, moment: datetime | None = None) -> int:
    moment = moment or datetime.now(timezone.utc)
    queued = 0
    for job in BackupJob.objects.using("control").filter(enabled=True).exclude(schedule=""):
        try:
            due = schedule_matches(job.schedule, moment, job.timezone)
        except ScheduleConfigurationError as exc:
            BackupJob.objects.using("control").filter(pk=job.pk).update(last_run_status=f"invalid: {exc}")
            continue
        if not due:
            continue
        key = f"scheduled-job:{job.slug}:{moment.astimezone(timezone.utc).strftime('%Y%m%d%H%M')}"
        with transaction.atomic(using="control"):
            operation, created = DataOperation.objects.using("control").get_or_create(
                kind=DataOperation.Kind.SYNC,
                idempotency_key=key,
                defaults={
                    "state": DataOperation.State.QUEUED,
                    "profile_key": job.target_profile_key,
                    "source_profile_key": job.source_profile_key,
                    "destination_profile_key": job.target_profile_key,
                    "checkpoint": {"job_slug": job.slug, "trigger": "schedule"},
                },
            )
            if not created:
                continue
            BackupJob.objects.using("control").filter(pk=job.pk).update(last_run_status="queued")
            DataOpsAuditEvent.objects.using("control").create(
                action="backup_job_queued",
                operation_id=operation.public_id,
                profile_key=job.target_profile_key,
                outcome="queued",
                evidence={"job_slug": job.slug, "trigger": "schedule"},
            )
            queued += 1
    return queued
