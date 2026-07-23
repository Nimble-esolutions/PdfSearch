"""Metrics endpoint exposing Prometheus-compatible data-lifecycle metrics."""

from __future__ import annotations

from django.conf import settings
from django.http import HttpResponse

from .models import ArtifactGeneration, MaintenanceJob, PDFFile


def metrics_view(request):
    """Expose data-lifecycle metrics in Prometheus text format."""
    lines = []
    env_identity = getattr(settings, "ENV_IDENTITY", None)

    def gauge(name, value, help_text=""):
        if help_text:
            lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name} {float(value) if value is not None else 0}")

    try:
        gauge("pdfsearch_backup_last_success_timestamp",
              _last_backup_timestamp(), "Unix timestamp of last successful backup")
    except Exception:
        gauge("pdfsearch_backup_last_success_timestamp", 0)

    try:
        gauge("pdfsearch_backup_dirty_age_seconds",
              _dirty_age_seconds(), "Seconds since data was last marked dirty")
    except Exception:
        gauge("pdfsearch_backup_dirty_age_seconds", 0)

    try:
        local = ArtifactGeneration.objects.filter(
            status__in=("active", "validated")
        ).order_by("-promoted_at", "-created_at").first()
        gauge("pdfsearch_current_generation_age_seconds",
              _age_seconds(local.created_at) if local else 0,
              "Age of current active generation in seconds")
    except Exception:
        gauge("pdfsearch_current_generation_age_seconds", 0)

    try:
        total_pdfs = PDFFile.objects.count()
        indexed = PDFFile.objects.filter(indexed=True).count()
        gauge("pdfsearch_data_pdf_count", total_pdfs)
        gauge("pdfsearch_data_indexed_pdf_count", indexed)
        gauge("pdfsearch_data_ready", 1 if total_pdfs == 0 or indexed > 0 else 0)
    except Exception:
        gauge("pdfsearch_data_ready", 0)

    try:
        gauge("pdfsearch_maintenance_queue_depth",
              MaintenanceJob.objects.filter(status__in=("queued", "running")).count())
    except Exception:
        gauge("pdfsearch_maintenance_queue_depth", 0)

    if env_identity:
        gauge("pdfsearch_backup_role", 1 if env_identity.is_backup_writer else 0)
        gauge("pdfsearch_is_production", 1 if env_identity.is_production else 0)

    return HttpResponse("\n".join(lines) + "\n", content_type="text/plain; charset=utf-8")


def _last_backup_timestamp() -> float:
    last = ArtifactGeneration.objects.order_by("-created_at").first()
    if last:
        return last.created_at.timestamp()
    return 0


def _dirty_age_seconds() -> float:
    from django.core.cache import cache
    raw = cache.get("pdfsearch:backup:dirty")
    if raw is None:
        return 0
    from datetime import datetime, timezone
    try:
        ts = float(raw)
        return datetime.now(timezone.utc).timestamp() - ts
    except (TypeError, ValueError):
        return 0


def _age_seconds(dt) -> float:
    from datetime import datetime, timezone
    if dt is None:
        return 0
    return (datetime.now(timezone.utc) - dt).total_seconds()
