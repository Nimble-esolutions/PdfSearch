#!/usr/bin/env python3
"""Build and repair the disposable maintenance-lifecycle browser fixture."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
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
    ArtifactGeneration,
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
    print(json.dumps(evidence, sort_keys=True))


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
    else:
        raise SystemExit(
            "expected seed-and-freeze, remove-retry-file, "
            "repair-retry-file, or activation-evidence"
        )
