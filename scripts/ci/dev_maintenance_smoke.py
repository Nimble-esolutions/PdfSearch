#!/usr/bin/env python3
"""Prove that the normal development worker consumes a local maintenance job."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "flowdocs.settings")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "flowdocs"))

import django

django.setup()

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db import close_old_connections

from core.maintenance import queue_job
from core.models import Folder, MaintenanceAuditEvent, PDFFile


def fail(message: str) -> None:
    print(f"[dev-maintenance-smoke] {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    user_model = get_user_model()
    actor = user_model.objects.create_user(
        username="dev-maintenance-smoke",
        role="superadmin",
        is_staff=True,
        is_superuser=True,
    )
    folder = Folder.objects.create(name="Development worker smoke", created_by=actor)
    document = PDFFile(
        title="Development worker smoke",
        folder=folder,
        uploaded_by=actor,
        category="other",
        indexed=False,
    )
    document.file.save(
        "dev-maintenance-smoke.pdf",
        ContentFile(b"%PDF-1.4\n%%EOF\n"),
        save=False,
    )
    document.save()

    job = queue_job(kind="validate", requested_by=actor, pdfs=[document])
    print(f"[dev-maintenance-smoke] queued {job.public_id}")

    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        close_old_connections()
        job.refresh_from_db()
        if job.status in {"completed", "failed", "cancelled"}:
            break
        time.sleep(0.5)
    else:
        fail(f"job {job.public_id} remained {job.status}")

    if job.status != "completed":
        fail(
            f"job {job.public_id} ended {job.status}: "
            f"{job.error_summary or 'no safe summary'}"
        )
    if job.completed_items != 1 or job.failed_items != 0:
        fail(
            f"job {job.public_id} counters were "
            f"{job.completed_items} completed/{job.failed_items} failed"
        )

    events = set(
        MaintenanceAuditEvent.objects.filter(job=job).values_list(
            "event_type", flat=True
        )
    )
    missing_events = {"claimed", "completed"} - events
    if missing_events:
        fail(f"job {job.public_id} lacks audit events: {sorted(missing_events)}")

    print(
        f"[dev-maintenance-smoke] job {job.public_id} completed "
        "through the separate worker service"
    )


if __name__ == "__main__":
    main()
