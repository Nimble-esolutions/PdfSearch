import hashlib
import json
import sqlite3
from pathlib import Path

from django.conf import settings
from django.contrib.auth import authenticate
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from core.models import Folder, PDFFile
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


class Command(BaseCommand):
    help = "Fail-closed smoke verification for the active runtime generation"

    def add_arguments(self, parser):
        parser.add_argument("--intent-id", required=True)

    def handle(self, *args, **options):
        intent_id = options["intent_id"]
        try:
            evidence = self._verify(intent_id)
        except (
            RuntimeControlError,
            SnapshotError,
            SearchDataIntegrityError,
            OSError,
            ValueError,
            sqlite3.Error,
        ) as exc:
            reason_code = getattr(
                exc, "reason_code", "activation_runtime_smoke_failed"
            )
            raise CommandError(reason_code) from exc
        self.stdout.write(
            self.style.SUCCESS(
                "Activation runtime verified: "
                f"{evidence['generation_id']}"
            )
        )

    def _verify(self, intent_id):
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
            or intent.get("target_manifest_digest")
            != active.manifest_digest
            or settings.RUNTIME_GENERATION_ID != active.generation_id
        ):
            raise RuntimeControlError(
                "activation_runtime_identity_mismatch"
            )
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
            raise RuntimeControlError(
                "activation_recovery_superadmin_unproven"
            )
        for pdf in PDFFile.objects.all().iterator():
            try:
                path = Path(pdf.file.path).resolve()
                path.relative_to(Path(settings.MEDIA_ROOT).resolve())
            except (OSError, ValueError) as exc:
                raise RuntimeControlError(
                    "activation_pdf_path_invalid"
                ) from exc
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
            raise RuntimeControlError(
                "activation_smoke_queries_changed"
            )
        payload = json.loads(raw_queries)
        for item in payload["queries"]:
            folder = None
            if item.get("folder_id"):
                folder = Folder.objects.filter(pk=item["folder_id"]).first()
            folder = folder or Folder.objects.order_by("pk").first()
            if folder is None:
                if item.get("allow_empty", False):
                    continue
                raise RuntimeControlError(
                    "activation_smoke_folder_missing"
                )
            answer, references = search_pdfs_fast(
                folder,
                item["query"],
                language=item["locale"],
            )
            if item.get("expect_references", True) and (
                not answer or not references
            ):
                raise RuntimeControlError(
                    "activation_search_probe_failed"
                )
        return {
            "generation_id": active.generation_id,
            "manifest_digest": active.manifest_digest,
            "database": "ok",
            "migrations": "ok",
            "recovery_superadmin": "ok",
            "pdfs": PDFFile.objects.count(),
            "faiss": "ok",
            "queries": len(payload["queries"]),
        }
