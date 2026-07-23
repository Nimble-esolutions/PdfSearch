from __future__ import annotations

import http.server
import json
import os
import signal
import socket
import sys
import threading
import time
from datetime import timedelta

from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.maintenance import claim_next_job, run_job
from core.models import MaintenanceJob, MaintenanceAuditEvent

HEARTBEAT_FILE = "/tmp/worker_heartbeat"
ORPHAN_TIMEOUT_MINUTES = 5
REDIS_QUEUE_KEY = "maintenance:queue"
HEALTHZ_PORT = 9090

_shutdown_flag = False


def _handle_shutdown(signum, frame):
    global _shutdown_flag
    _shutdown_flag = True


def _write_heartbeat():
    try:
        with open(HEARTBEAT_FILE, "w") as f:
            f.write(f"{os.getpid()}\n{int(time.time())}")
    except OSError:
        pass


def _get_redis_client():
    try:
        return cache.client.get_client()
    except Exception:
        return None


def _recover_orphaned_jobs():
    cutoff = timezone.now() - timedelta(minutes=ORPHAN_TIMEOUT_MINUTES)
    orphaned = MaintenanceJob.objects.filter(
        status="running",
        started_at__lt=cutoff,
    )
    for job in orphaned:
        job.status = "queued"
        job.started_at = None
        job.save(update_fields=["status", "started_at"])
        MaintenanceAuditEvent.objects.create(
            job=job,
            event_type="worker_died",
            payload={
                "previous_status": "running",
                "note": "Recovered after worker restart; previous run may have crashed.",
            },
        )
        job.items.filter(status="running").update(
            status="queued", started_at=None
        )
    return orphaned.count()


STATS = {
    "started_at": None,
    "jobs_processed": 0,
    "last_job_id": None,
    "last_job_kind": None,
    "last_job_status": None,
    "shutting_down": False,
}


def _claim_via_redis():
    client = _get_redis_client()
    if client is None:
        return None
    try:
        result = client.blpop(REDIS_QUEUE_KEY, timeout=3)
        if result is None:
            return None
        job_id_bytes = result[1]
        job_id = job_id_bytes.decode() if isinstance(job_id_bytes, bytes) else job_id_bytes
        return claim_next_job()
    except Exception:
        return claim_next_job()


class _HealthHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path != "/healthz":
            self.send_response(404)
            self.end_headers()
            return
        payload = {
            "status": "shutting_down" if _shutdown_flag else "alive",
            "uptime_seconds": int(time.time() - (STATS["started_at"] or time.time())),
            "last_heartbeat_age_seconds": _heartbeat_age(),
            "jobs_processed": STATS["jobs_processed"],
            "last_job_id": STATS["last_job_id"],
            "last_job_kind": STATS["last_job_kind"],
            "last_job_status": STATS["last_job_status"],
            "queue_sqlite_queued": MaintenanceJob.objects.filter(status="queued").count(),
            "queue_redis_length": _redis_queue_length(),
        }
        body = json.dumps(payload, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _heartbeat_age():
    try:
        with open(HEARTBEAT_FILE) as f:
            lines = f.read().strip().split("\n")
            return int(time.time()) - int(lines[1])
    except Exception:
        return 999


def _redis_queue_length():
    client = _get_redis_client()
    if client is None:
        return -1
    try:
        return client.llen(REDIS_QUEUE_KEY)
    except Exception:
        return -1


def _start_health_server():
    for attempt in range(5):
        try:
            server = http.server.HTTPServer(
                ("127.0.0.1", HEALTHZ_PORT), _HealthHandler
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            return server
        except OSError:
            if attempt < 4:
                time.sleep(1)
            else:
                print(f"[worker] health server could not bind :{HEALTHZ_PORT}", file=sys.stderr)
    return None


class Command(BaseCommand):
    help = "Run queued PdfSearch maintenance jobs"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--poll-seconds", type=float, default=3.0)
        parser.add_argument(
            "--kinds",
            help="Comma-separated list of job kinds to process (others ignored)",
        )
        parser.add_argument(
            "--exclude-kinds",
            help="Comma-separated list of job kinds to skip",
        )

    def handle(self, *args, **options):
        signal.signal(signal.SIGTERM, _handle_shutdown)
        signal.signal(signal.SIGINT, _handle_shutdown)

        STATS["started_at"] = int(time.time())

        _start_health_server()

        recovered = _recover_orphaned_jobs()
        if recovered:
            self.stdout.write(
                self.style.WARNING(
                    f"Recovered {recovered} orphaned job(s) from prior shutdown."
                )
            )

        allowed_kinds = None
        if options["kinds"]:
            allowed_kinds = {k.strip() for k in options["kinds"].split(",") if k.strip()}
        excluded_kinds = set()
        if options["exclude_kinds"]:
            excluded_kinds = {k.strip() for k in options["exclude_kinds"].split(",") if k.strip()}

        while not _shutdown_flag:
            job = _claim_via_redis()
            if job is None:
                _write_heartbeat()
                if options["once"]:
                    return
                time.sleep(max(options["poll_seconds"], 0.5))
                continue

            if allowed_kinds and job.kind not in allowed_kinds:
                continue
            if job.kind in excluded_kinds:
                continue

            self.stdout.write(
                self.style.NOTICE(
                    f"[worker] Running job {job.public_id} ({job.kind})"
                )
            )
            STATS["last_job_id"] = str(job.public_id)
            STATS["last_job_kind"] = job.kind

            finished = run_job(job)
            STATS["jobs_processed"] += 1
            STATS["last_job_status"] = finished.status

            self.stdout.write(
                self.style.SUCCESS(
                    f"Job {finished.public_id} {finished.status}: "
                    f"{finished.completed_items} completed, {finished.failed_items} failed"
                )
            )
            _write_heartbeat()

            if options["once"]:
                return

        STATS["shutting_down"] = True
        self.stdout.write(self.style.WARNING("Shutting down maintenance worker."))