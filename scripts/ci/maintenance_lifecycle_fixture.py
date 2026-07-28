#!/usr/bin/env python3
"""Build and repair the disposable maintenance-lifecycle browser fixture."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, "/app/flowdocs")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "flowdocs.settings")

import django

django.setup()

import fitz
from django.conf import settings
from django.utils import timezone

from core.models import CustomUser, Folder, PDFFile
from core.utils import precompute_pdf_embeddings
from vaultops.models import (
    ActivationIntent,
    ArtifactGeneration,
    ConfirmationChallenge,
    RuntimePointerObservation,
    VaultConnectionProfile,
)
from vaultops.runtime_control import (
    atomic_write_json,
    build_runtime_pointer,
    runtime_control_paths,
    read_signed_document,
)


GENERATION_ID = "maintenance-e2e-parent"
MANIFEST_DIGEST = hashlib.sha256(GENERATION_ID.encode()).hexdigest()
USERNAME = "ci-admin"
PASSWORD = "ci-only-password-not-for-production"
PARENT_EVIDENCE = Path(settings.DATA_CONTROL_ROOT) / "e2e-parent-tree.json"


def _pdf(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 90), text, fontsize=12)
    document.save(path)
    document.close()


def _copy_database(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    target_connection = sqlite3.connect(target)
    try:
        source_connection.backup(target_connection)
    finally:
        target_connection.close()
        source_connection.close()


def _tree_records(root: Path) -> list[dict]:
    records = []
    for path in sorted(root.rglob("*"), key=lambda value: value.as_posix()):
        relative = path.relative_to(root).as_posix()
        stat_result = path.lstat()
        record = {
            "path": relative,
            "mode": stat_result.st_mode & 0o777,
            "type": "directory" if path.is_dir() else "file",
            "size": stat_result.st_size,
        }
        if path.is_file():
            record["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        records.append(record)
    return records


def seed_and_freeze() -> None:
    user, _ = CustomUser.objects.update_or_create(
        username=USERNAME,
        defaults={
            "role": "superadmin",
            "is_staff": True,
            "is_superuser": True,
            "is_active": True,
        },
    )
    user.set_password(PASSWORD)
    user.save(update_fields=["password"])
    folder, _ = Folder.objects.get_or_create(
        name="Lifecycle Evidence",
        defaults={
            "created_by": user,
            "keywords": ["cooperative", "सहकार"],
        },
    )
    good_path = Path(settings.MEDIA_ROOT) / "pdfs/e2e-good.pdf"
    missing_path = Path(settings.MEDIA_ROOT) / "pdfs/e2e-retry.pdf"
    _pdf(
        good_path,
        "Cooperative audit evidence. सहकारी लेखापरीक्षण पुरावा.",
    )
    _pdf(
        missing_path,
        "Member register evidence. सभासद नोंदवही पुरावा.",
    )
    for title, name in (
        ("Bilingual audit evidence", "pdfs/e2e-good.pdf"),
        ("Retry checkpoint evidence", "pdfs/e2e-retry.pdf"),
    ):
        pdf, _ = PDFFile.objects.update_or_create(
            title=title,
            folder=folder,
            defaults={
                "file": name,
                "file_path": name,
                "uploaded_by": user,
                "category": "audit",
                "subject": "cooperation",
                "keywords": ["audit", "लेखापरीक्षण"],
                "lifecycle": "ready",
            },
        )
        precompute_pdf_embeddings(pdf)
    runtime = (
        Path(settings.RUNTIME_GENERATIONS_ROOT)
        / f"{GENERATION_ID}-{MANIFEST_DIGEST[:12]}"
    )
    if runtime.exists():
        shutil.rmtree(runtime)
    runtime.mkdir(parents=True)
    _copy_database(
        Path(settings.DATABASES["default"]["NAME"]),
        runtime / "db.sqlite3",
    )
    for name, source in (
        ("media", Path(settings.MEDIA_ROOT)),
        ("pdf_cache", Path(settings.PDF_CACHE_DIR)),
        ("faiss_indexes", Path(settings.FAISS_INDEX_DIR)),
        ("chroma_db", Path(settings.CHROMA_DIR)),
    ):
        shutil.copytree(source, runtime / name, dirs_exist_ok=True)
    (runtime / "runtime-evidence.json").write_text(
        (
            '{"generation_id":"%s","manifest_digest":"%s",'
            '"profile_fingerprint":"%s","sanitized":true,'
            '"static_assets_posture":"custody_only"}'
        )
        % (GENERATION_ID, MANIFEST_DIGEST, "f" * 64),
        encoding="utf-8",
    )
    for path in sorted(runtime.rglob("*"), reverse=True):
        path.chmod(0o550 if path.is_dir() else 0o440)
        os.chown(path, 1000, 1000)
    runtime.chmod(0o550)
    os.chown(runtime, 1000, 1000)
    runtime.parent.chmod(0o750)
    os.chown(runtime.parent, 1000, 1000)
    profile, _ = VaultConnectionProfile.objects.update_or_create(
        key=settings.VAULT_DEFAULT_PROFILE,
        defaults={
            "display_name": "Disposable lifecycle profile",
            "dataset_id": settings.ENV_IDENTITY.dataset_id,
            "fingerprint": "f" * 64,
        },
    )
    generation, _ = ArtifactGeneration.objects.update_or_create(
        profile=profile,
        dataset_id=profile.dataset_id,
        generation_id=GENERATION_ID,
        defaults={
            "manifest_digest": MANIFEST_DIGEST,
            "runtime_state": ArtifactGeneration.RuntimeState.ACTIVE,
            "local_presence": ArtifactGeneration.LocalPresence.PREPARED,
            "deployment_id": settings.ENV_IDENTITY.deployment_id,
        },
    )
    pointer = build_runtime_pointer(
        deployment_id=settings.ENV_IDENTITY.deployment_id,
        generation_id=generation.generation_id,
        manifest_digest=generation.manifest_digest,
        runtime_path=runtime,
        intent_digest="maintenance-e2e-bootstrap",
        state_version=1,
        signing_key=settings.ACTIVATION_INTENT_SIGNING_KEY,
    )
    active_path = runtime_control_paths(settings.DATA_CONTROL_ROOT)["active"]
    atomic_write_json(active_path, pointer)
    os.chown(active_path, 1000, 1000)
    control_root = Path(settings.DATA_CONTROL_ROOT).resolve()
    parent = active_path.parent
    while parent != control_root.parent:
        os.chown(parent, 1000, 1000)
        if parent == control_root:
            break
        parent = parent.parent
    RuntimePointerObservation.objects.update_or_create(
        deployment_id=settings.ENV_IDENTITY.deployment_id,
        defaults={
            "active_generation_id": generation.generation_id,
            "previous_generation_id": "",
            "pointer_digest": pointer["document_digest"],
            "status": "ready",
            "observed_at": timezone.now(),
        },
    )
    PARENT_EVIDENCE.write_text(
        json.dumps(
            {
                "runtime_path": str(runtime),
                "records": _tree_records(runtime),
                "vault_projection": list(
                    ArtifactGeneration.objects.using("control")
                    .values(
                        "generation_id",
                        "manifest_digest",
                        "vault_state",
                    )
                    .order_by("generation_id")
                ),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    os.chown(PARENT_EVIDENCE, 1000, 1000)
    print("fixture_ready")


def remove_retry_file() -> None:
    path = Path(settings.MEDIA_ROOT) / "pdfs/e2e-retry.pdf"
    if path.exists():
        path.unlink()
    print("retry_file_removed")


def repair_retry_file() -> None:
    _pdf(
        Path(settings.MEDIA_ROOT) / "pdfs/e2e-retry.pdf",
        "Member register evidence. सभासद नोंदवही पुरावा.",
    )
    print("retry_file_restored")


def activation_evidence() -> None:
    """Print bounded, secret-free role/barrier evidence for a failed run."""
    paths = runtime_control_paths(settings.DATA_CONTROL_ROOT)
    evidence = {"intents": [], "acks": [], "results": [], "lock": {}}
    for kind, key in (
        ("activation_intent", "intents"),
        ("activation_ack", "acks"),
        ("activation_result", "results"),
    ):
        for path in sorted(paths[key].glob("*.json")):
            try:
                document = read_signed_document(
                    path,
                    signing_key=settings.ACTIVATION_INTENT_SIGNING_KEY,
                    expected_kind=kind,
                    deployment_id=settings.ENV_IDENTITY.deployment_id,
                )
                evidence[key].append(
                    {
                        "file": path.name,
                        "intent_id": document.get("intent_id", ""),
                        "role": document.get("role", ""),
                        "state": document.get("state", ""),
                        "status": document.get("status", ""),
                        "safe_error_code": document.get("safe_error_code", ""),
                    }
                )
            except Exception as exc:
                evidence[key].append(
                    {"file": path.name, "error": type(exc).__name__}
                )
    lock = paths["lock"]
    if lock.exists() and lock.is_file() and not lock.is_symlink():
        try:
            value = json.loads(lock.read_text(encoding="utf-8"))
            evidence["lock"] = {
                "intent_id": value.get("intent_id", ""),
                "present": True,
            }
        except (OSError, ValueError):
            evidence["lock"] = {"present": True, "state": "unreadable"}
    evidence["control_intents"] = list(
        ActivationIntent.objects.using("control")
        .values(
            "public_id",
            "state",
            "safe_error_code",
            "target_generation_id",
            "previous_generation_id",
        )
        .order_by("created_at")
    )
    for item in evidence["control_intents"]:
        item["public_id"] = str(item["public_id"])
    print(json.dumps(evidence, sort_keys=True))


def assert_parent_tree() -> None:
    expected = json.loads(PARENT_EVIDENCE.read_text(encoding="utf-8"))
    actual = _tree_records(Path(expected["runtime_path"]))
    if actual != expected["records"]:
        expected_by_path = {
            item["path"]: item for item in expected["records"]
        }
        actual_by_path = {item["path"]: item for item in actual}
        changed_paths = sorted(
            path
            for path in expected_by_path.keys() | actual_by_path.keys()
            if expected_by_path.get(path) != actual_by_path.get(path)
        )
        raise SystemExit(
            "parent_runtime_tree_changed:"
            + ",".join(changed_paths[:20])
        )
    print("parent_runtime_tree_unchanged")


def assert_maintenance_evidence() -> None:
    from core.models import MaintenanceJob

    job = MaintenanceJob.objects.filter(kind="reindex_selected").latest(
        "created_at"
    )
    items = list(job.items.order_by("pdf_id"))
    if job.status != "completed" or len(items) != 2:
        raise SystemExit("maintenance_job_scope_invalid")
    if any(
        item.attempts != 1
        or item.started_at is None
        or item.finished_at is None
        for item in items
    ):
        raise SystemExit("maintenance_checkpoint_recomputed")
    attempts = job.options.get("candidate_folder_build_attempts", {})
    if list(attempts.values()) != [2]:
        raise SystemExit("maintenance_folder_build_count_invalid")
    print("maintenance_checkpoint_evidence_verified")


def assert_final_control_evidence() -> None:
    from vaultops.runtime_control import read_runtime_pointer

    expected = json.loads(PARENT_EVIDENCE.read_text(encoding="utf-8"))
    paths = runtime_control_paths(settings.DATA_CONTROL_ROOT)
    active = read_runtime_pointer(
        paths["active"],
        deployment_id=settings.ENV_IDENTITY.deployment_id,
        signing_key=settings.ACTIVATION_INTENT_SIGNING_KEY,
        runtime_root=settings.RUNTIME_GENERATIONS_ROOT,
    )
    previous = read_runtime_pointer(
        paths["previous"],
        deployment_id=settings.ENV_IDENTITY.deployment_id,
        signing_key=settings.ACTIVATION_INTENT_SIGNING_KEY,
        runtime_root=settings.RUNTIME_GENERATIONS_ROOT,
    )
    if active.generation_id != GENERATION_ID or not previous.generation_id.startswith(
        "lm-"
    ):
        raise SystemExit("signed_pointer_pair_invalid")
    intents = list(
        ActivationIntent.objects.using("control").order_by("created_at")
    )
    if len(intents) != 2 or any(
        intent.state != ActivationIntent.State.COMMITTED for intent in intents
    ):
        raise SystemExit("activation_intents_not_terminal")
    if any(not intent.idempotency_key for intent in intents):
        raise SystemExit("activation_idempotency_missing")
    for intent in intents:
        recovery_set_id = intent.checkpoint.get("recovery_set_id", "")
        manifest_path = (
            Path(settings.RECOVERY_SET_ROOT)
            / recovery_set_id
            / "recovery-set.json"
        )
        if not recovery_set_id or not manifest_path.is_file():
            raise SystemExit("activation_recovery_evidence_missing")
        recovery_manifest = json.loads(
            manifest_path.read_text(encoding="utf-8")
        )
        created_at = datetime.fromisoformat(
            recovery_manifest["created_at"]
        )
        if created_at > intent.created_at:
            raise SystemExit("activation_recovery_not_pre_intent")
    if ConfirmationChallenge.objects.using("control").filter(
        used_at__isnull=False
    ).count() < 2:
        raise SystemExit("confirmation_evidence_missing")
    original = {
        item["generation_id"]: item
        for item in expected["vault_projection"]
    }
    current = {
        item["generation_id"]: item
        for item in ArtifactGeneration.objects.using("control")
        .filter(generation_id__in=original)
        .values("generation_id", "manifest_digest", "vault_state")
    }
    if current != original:
        raise SystemExit("vault_projection_changed")
    if ArtifactGeneration.objects.using("control").filter(
        origin=ArtifactGeneration.Origin.LOCAL_MAINTENANCE
    ).exclude(vault_state=ArtifactGeneration.VaultState.UNKNOWN).exists():
        raise SystemExit("local_candidate_moved_vault_authority")
    print("final_signed_control_evidence_verified")


if __name__ == "__main__":
    operation = sys.argv[1] if len(sys.argv) > 1 else ""
    if operation == "seed-and-freeze":
        seed_and_freeze()
    elif operation == "remove-retry-file":
        remove_retry_file()
    elif operation == "repair-retry-file":
        repair_retry_file()
    elif operation == "activation-evidence":
        activation_evidence()
    elif operation == "assert-parent-tree":
        assert_parent_tree()
    elif operation == "assert-maintenance-evidence":
        assert_maintenance_evidence()
    elif operation == "assert-final-control-evidence":
        assert_final_control_evidence()
    else:
        raise SystemExit(
            "expected seed-and-freeze, remove-retry-file, "
            "repair-retry-file, activation-evidence, assert-parent-tree, "
            "assert-maintenance-evidence, or assert-final-control-evidence"
        )
