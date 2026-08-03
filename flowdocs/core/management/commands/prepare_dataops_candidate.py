"""Internal command: prepare one verified DataOps restore for activation."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.candidate_maintenance import validate_candidate
from core.maintenance import queue_job, run_job
from core.models import (
    Folder,
    MaintenanceJob,
    MaintenanceJobItem,
    PDFFile,
    SEARCHABLE_PDF_LIFECYCLES,
)
from core.search_artifact_health import classify_search_artifacts
from dataops.package_v3 import canonical_json_bytes


EVIDENCE_VERSION = 2


def _json_object(path: Path, code: str):
    try:
        payload = json.loads(path.read_bytes())
    except (OSError, TypeError, ValueError) as exc:
        raise CommandError(code) from exc
    if not isinstance(payload, dict):
        raise CommandError(code)
    return payload


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _quarantine_legacy_indexes(workspace: Path) -> None:
    quarantine = workspace / "derived-quarantine"
    quarantine.mkdir(exist_ok=True, mode=0o700)
    for name in ("faiss_indexes", "chroma_db"):
        source = workspace / name
        retained = quarantine / f"legacy-{name}"
        if source.exists() and not retained.exists():
            os.replace(source, retained)
        elif source.is_symlink():
            raise CommandError("candidate_derived_path_unsafe")
        source.mkdir(exist_ok=True, mode=0o700)


def _text_coverage() -> dict[str, int]:
    latin = devanagari = extracted = ocr = 0
    for text, metadata in PDFFile.objects.values_list("extracted_text", "ocr_metadata"):
        value = str(text or "")
        extracted += int(bool(value.strip()))
        latin += int(bool(re.search(r"[A-Za-z]", value)))
        devanagari += int(bool(re.search(r"[\u0900-\u097F]", value)))
        ocr += int(bool(metadata))
    return {
        "documents_with_text": extracted,
        "documents_with_latin_text": latin,
        "documents_with_devanagari_text": devanagari,
        "documents_with_ocr_evidence": ocr,
    }


def _reindexed_document_count(manifest_digest: str) -> int:
    job_ids = MaintenanceJob.objects.filter(
        kind="reindex_needed",
        scope__dataops_manifest_sha256=manifest_digest,
    ).values_list("pk", flat=True)
    return MaintenanceJobItem.objects.filter(
        job_id__in=job_ids,
        status="completed",
        pdf_id__isnull=False,
    ).values("pdf_id").distinct().count()


class Command(BaseCommand):
    help = "Internal: prepare one isolated DataOps restore candidate"

    def add_arguments(self, parser):
        parser.add_argument("--manifest-digest", required=True)
        parser.add_argument(
            "--maximum-external-documents",
            type=int,
            default=500,
        )

    def handle(self, *args, **options):
        if os.environ.get("MAINTENANCE_CANDIDATE_EXECUTION") != "1":
            raise CommandError("candidate_execution_environment_required")
        workspace = Path(settings.DATA_ROOT)
        if workspace.is_symlink() or not workspace.is_dir():
            raise CommandError("candidate_workspace_invalid")
        workspace = workspace.resolve()
        manifest_digest = str(options["manifest_digest"] or "")
        if not re.fullmatch(r"[0-9a-f]{64}", manifest_digest):
            raise CommandError("candidate_manifest_digest_invalid")
        restore = _json_object(
            workspace / ".dataops-restore.json",
            "candidate_restore_receipt_invalid",
        )
        rehearsal = _json_object(
            workspace / ".dataops-rehearsal.json",
            "candidate_rehearsal_receipt_invalid",
        )
        if (
            restore.get("verified") is not True
            or restore.get("manifest_sha256") != manifest_digest
            or rehearsal.get("success") is not True
            or rehearsal.get("manifest_sha256") != manifest_digest
        ):
            raise CommandError("candidate_evidence_mismatch")
        receipt_path = workspace / ".dataops-candidate.json"
        if receipt_path.is_file() and not receipt_path.is_symlink():
            existing = _json_object(receipt_path, "candidate_receipt_invalid")
            if (
                existing.get("success") is True
                and existing.get("manifest_sha256") == manifest_digest
                and existing.get("indexing_ratio") == 1.0
            ):
                validation = validate_candidate(workspace)
                if existing.get("evidence_version") != EVIDENCE_VERSION:
                    existing.update(
                        {
                            "evidence_version": EVIDENCE_VERSION,
                            "reindexed_documents": _reindexed_document_count(
                                manifest_digest
                            ),
                            "validation": validation,
                            "text_coverage": _text_coverage(),
                        }
                    )
                    temporary = receipt_path.with_name(
                        f".{receipt_path.name}.partial"
                    )
                    temporary.write_bytes(canonical_json_bytes(existing) + b"\n")
                    os.chmod(temporary, 0o600)
                    os.replace(temporary, receipt_path)
                self.stdout.write("candidate_reused")
                return
            raise CommandError("candidate_receipt_conflict")

        reindex = []
        blocked = []
        searchable = PDFFile.objects.filter(
            lifecycle__in=SEARCHABLE_PDF_LIFECYCLES,
        ).order_by("pk")
        for pdf in searchable.iterator(chunk_size=100):
            try:
                media_exists = bool(
                    pdf.file
                    and pdf.file.name
                    and pdf.file.storage.exists(pdf.file.name)
                )
            except (OSError, ValueError):
                media_exists = False
            health = classify_search_artifacts(
                lifecycle=pdf.lifecycle,
                indexed=pdf.indexed,
                media_exists=media_exists,
                page_chunks=pdf.page_chunks,
                chunk_embeddings=pdf.chunk_embeddings,
            )
            if health.blocking:
                blocked.append(pdf.pk)
            elif health.reindex_required:
                reindex.append(pdf)
        if blocked:
            raise CommandError("candidate_media_blocked")
        maximum = int(options["maximum_external_documents"])
        if maximum < 0 or len(reindex) > maximum:
            raise CommandError("candidate_external_budget_exceeded")
        if reindex and not getattr(settings, "EXTERNAL_EMBEDDINGS_ENABLED", False):
            raise CommandError("candidate_external_embeddings_disabled")

        _quarantine_legacy_indexes(workspace)
        reindex_job = None
        if reindex:
            reindex_job = queue_job(
                kind="reindex_needed",
                requested_by=None,
                pdfs=reindex,
                scope={"dataops_manifest_sha256": manifest_digest},
                options={"dataops_candidate": True},
            )
            reindex_job = run_job(reindex_job)
            if reindex_job.status != "completed":
                raise CommandError("candidate_reindex_failed")
        folders = list(
            Folder.objects.filter(
                files__lifecycle__in=SEARCHABLE_PDF_LIFECYCLES,
            ).distinct().order_by("pk")
        )
        repair_job = queue_job(
            kind="repair_indexes",
            requested_by=None,
            folders=folders,
            scope={"dataops_manifest_sha256": manifest_digest},
            options={"dataops_candidate": True},
        )
        repair_job = run_job(repair_job)
        if repair_job.status != "completed":
            raise CommandError("candidate_index_repair_failed")
        validation = validate_candidate(workspace)
        total = searchable.count()
        indexed = searchable.filter(
            indexed=True,
            processing_status="ready",
        ).count()
        ratio = 1.0 if total == 0 else round(indexed / total, 4)
        if ratio != 1.0:
            raise CommandError("candidate_indexing_incomplete")
        receipt = {
            "schema_version": 3,
            "evidence_version": EVIDENCE_VERSION,
            "success": True,
            "manifest_sha256": manifest_digest,
            "database_sha256": _sha256(workspace / "db.sqlite3"),
            "documents": total,
            "reindexed_documents": _reindexed_document_count(manifest_digest),
            "reindex_attempted_this_run": len(reindex),
            "repaired_folders": len(folders),
            "indexing_ratio": ratio,
            "text_coverage": _text_coverage(),
            "validation": validation,
            "jobs": {
                "reindex": str(reindex_job.public_id) if reindex_job else "",
                "repair": str(repair_job.public_id),
            },
        }
        temporary = receipt_path.with_name(f".{receipt_path.name}.partial")
        temporary.write_bytes(canonical_json_bytes(receipt) + b"\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, receipt_path)
        self.stdout.write("candidate_prepared")
