import hashlib
import json
import sqlite3
from pathlib import Path

from django.conf import settings
from django.contrib.auth import authenticate
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from core.models import Folder, PDFFile
from core.media_quarantine import (
    build_unavailable_attestation,
    storage_key_status,
)
from core.utils import SearchDataIntegrityError, search_pdfs_fast
from vaultops.runtime_control import (
    RuntimeControlError,
    read_runtime_pointer,
    read_signed_document,
    runtime_control_paths,
)
from vaultops.services.snapshot import (
    SnapshotError,
    _validate_faiss_coherence,
)


RUNTIME_VERIFICATION_ERRORS = (
    RuntimeControlError,
    SnapshotError,
    SearchDataIntegrityError,
    OSError,
    ValueError,
    sqlite3.Error,
)


def verify_activation_runtime(intent_id):
    """Verify the active runtime and return bounded, content-free evidence."""
    paths = runtime_control_paths(settings.DATA_CONTROL_ROOT)
    intent = read_signed_document(
        paths["intents"] / f"{intent_id}.json",
        signing_key=settings.ACTIVATION_INTENT_SIGNING_KEY,
        expected_kind="activation_intent",
        deployment_id=settings.ENV_IDENTITY.deployment_id,
    )
    active = read_runtime_pointer(
        paths["active"],
        deployment_id=settings.ENV_IDENTITY.deployment_id,
        signing_key=settings.ACTIVATION_INTENT_SIGNING_KEY,
        runtime_root=settings.RUNTIME_GENERATIONS_ROOT,
    )
    if (
        intent.get("target_generation_id") != active.generation_id
        or intent.get("target_manifest_digest") != active.manifest_digest
        or settings.RUNTIME_GENERATION_ID != active.generation_id
        or settings.RUNTIME_MANIFEST_DIGEST != active.manifest_digest
    ):
        raise RuntimeControlError("activation_runtime_identity_mismatch")
    with connection.cursor() as cursor:
        cursor.execute("PRAGMA integrity_check")
        integrity = cursor.fetchone()
        cursor.execute("PRAGMA foreign_key_check")
        foreign_keys = cursor.fetchall()
    if not integrity or integrity[0] != "ok" or foreign_keys:
        raise RuntimeControlError("activation_runtime_database_invalid")
    executor = MigrationExecutor(connection)
    if executor.migration_plan(executor.loader.graph.leaf_nodes()):
        raise RuntimeControlError("activation_runtime_migrations_pending")
    user = authenticate(
        username=settings.ACTIVATION_RECOVERY_SUPERADMIN_USERNAME,
        password=settings.ACTIVATION_RECOVERY_SUPERADMIN_PASSWORD,
    )
    if (
        user is None
        or not user.is_active
        or not user.is_superuser
        or user.role != "superadmin"
    ):
        raise RuntimeControlError("activation_recovery_superadmin_unproven")
    unavailable_records = [
        {
            "id": pdf_id,
            "lifecycle": lifecycle,
            "storage_key_status": storage_key_status(file_name),
        }
        for pdf_id, lifecycle, file_name in (
        PDFFile.objects.filter(lifecycle="unavailable")
        .order_by("pk")
        .values_list("pk", "lifecycle", "file")
        )
    ]
    for pdf in PDFFile.objects.exclude(lifecycle="unavailable").iterator():
        try:
            path = Path(pdf.file.path).resolve()
            path.relative_to(Path(settings.MEDIA_ROOT).resolve())
        except (OSError, ValueError) as exc:
            raise RuntimeControlError("activation_pdf_path_invalid") from exc
        if not path.is_file():
            raise RuntimeControlError("activation_pdf_missing")
    _validate_faiss_coherence(
        Path(settings.DATABASES["default"]["NAME"]),
        Path(settings.FAISS_INDEX_DIR),
    )
    query_path = Path(settings.ACTIVATION_SMOKE_QUERIES_FILE)
    raw_queries = query_path.read_bytes()
    if hashlib.sha256(raw_queries).hexdigest() != intent.get(
        "smoke_queries_digest"
    ):
        raise RuntimeControlError("activation_smoke_queries_changed")
    payload = json.loads(raw_queries)
    queries = payload.get("queries") if isinstance(payload, dict) else None
    if not isinstance(queries, list):
        raise RuntimeControlError("activation_smoke_queries_invalid")
    query_results = []
    for item in queries:
        if (
            not isinstance(item, dict)
            or item.get("locale") not in {"en", "mr"}
            or not isinstance(item.get("query"), str)
            or not item["query"].strip()
        ):
            raise RuntimeControlError("activation_smoke_queries_invalid")
        folder = None
        if item.get("folder_id"):
            folder = Folder.objects.filter(pk=item["folder_id"]).first()
        folder = folder or Folder.objects.order_by("pk").first()
        query_evidence = {
            "locale": item["locale"],
            "query_sha256": hashlib.sha256(
                item["query"].encode("utf-8")
            ).hexdigest(),
            "answer_present": False,
            "reference_count": 0,
            "status": "skipped",
        }
        if folder is None:
            if item.get("allow_empty", False):
                query_results.append(query_evidence)
                continue
            raise RuntimeControlError("activation_smoke_folder_missing")
        answer, references = search_pdfs_fast(
            folder,
            item["query"],
            language=item["locale"],
        )
        query_evidence.update(
            {
                "answer_present": bool(answer),
                "reference_count": len(references or []),
                "status": "passed",
            }
        )
        if item.get("expect_references", True) and (
            not answer or not references
        ):
            raise RuntimeControlError("activation_search_probe_failed")
        query_results.append(query_evidence)
    return {
        "schema_version": 1,
        "generation_id": active.generation_id,
        "manifest_digest": active.manifest_digest,
        "database": "ok",
        "migrations": "ok",
        "recovery_superadmin": "ok",
        "pdfs": PDFFile.objects.exclude(lifecycle="unavailable").count(),
        "unavailable_documents": build_unavailable_attestation(
            unavailable_records
        ),
        "faiss": "ok",
        "queries": query_results,
        "executed_locales": sorted(
            {
                item["locale"]
                for item in query_results
                if item["status"] == "passed"
            }
        ),
    }
