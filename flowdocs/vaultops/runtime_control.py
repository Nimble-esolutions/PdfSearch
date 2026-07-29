"""Pure-stdlib runtime pointer and activation-intent protocol.

This module is safe to import before Django settings. Supervisors and Django
both use the same canonical signing and path-validation implementation.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


MAX_CONTROL_DOCUMENT_BYTES = 128 * 1024
RUNTIME_EVIDENCE_FILE = "runtime-evidence.json"
RUNTIME_DIRECTORIES = {
    "media": "media",
    "pdf_cache": "pdf_cache",
    "faiss_indexes": "faiss_indexes",
    "chroma_db": "chroma_db",
}


class RuntimeControlError(RuntimeError):
    reason_code = "runtime_control_invalid"

    def __init__(self, reason_code=None):
        self.reason_code = reason_code or self.reason_code
        super().__init__(self.reason_code)


@dataclass(frozen=True)
class ResolvedRuntime:
    generation_id: str
    manifest_digest: str
    runtime_path: Path
    database_path: Path
    media_root: Path
    pdf_cache_dir: Path
    faiss_index_dir: Path
    chroma_dir: Path
    pointer_digest: str
    intent_digest: str


def canonical_bytes(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _unsigned(document):
    return {
        key: value
        for key, value in document.items()
        if key not in {"document_digest", "signature"}
    }


def sign_document(payload, signing_key):
    if not isinstance(signing_key, str) or len(signing_key) < 32:
        raise RuntimeControlError("activation_signing_key_invalid")
    unsigned = dict(payload)
    digest = hashlib.sha256(canonical_bytes(unsigned)).hexdigest()
    signature = hmac.new(
        signing_key.encode("utf-8"),
        digest.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return {
        **unsigned,
        "document_digest": digest,
        "signature": signature,
    }


def verify_document(
    document,
    *,
    signing_key,
    expected_kind,
    deployment_id,
):
    if not isinstance(document, dict):
        raise RuntimeControlError("control_document_malformed")
    unsigned = _unsigned(document)
    digest = hashlib.sha256(canonical_bytes(unsigned)).hexdigest()
    supplied_digest = document.get("document_digest", "")
    expected_signature = hmac.new(
        signing_key.encode("utf-8"),
        digest.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    if (
        len(signing_key) < 32
        or not hmac.compare_digest(str(supplied_digest), digest)
        or not hmac.compare_digest(
            str(document.get("signature", "")),
            expected_signature,
        )
    ):
        raise RuntimeControlError("control_document_signature_invalid")
    if (
        document.get("schema_version") != 1
        or document.get("kind") != expected_kind
        or document.get("deployment_id") != deployment_id
    ):
        raise RuntimeControlError("control_document_identity_mismatch")
    return digest


def _read_json(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise RuntimeControlError("control_document_missing")
    file_stat = os.stat(path, follow_symlinks=False)
    if (
        not stat.S_ISREG(file_stat.st_mode)
        or file_stat.st_nlink != 1
        or file_stat.st_size > MAX_CONTROL_DOCUMENT_BYTES
    ):
        raise RuntimeControlError("control_document_unsafe")
    try:
        data = path.read_bytes()
        if len(data) > MAX_CONTROL_DOCUMENT_BYTES:
            raise RuntimeControlError("control_document_too_large")
        value = json.loads(data)
    except RuntimeControlError:
        raise
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise RuntimeControlError("control_document_malformed") from exc
    if not isinstance(value, dict):
        raise RuntimeControlError("control_document_malformed")
    return value


def read_signed_document(
    path,
    *,
    signing_key,
    expected_kind,
    deployment_id,
):
    document = _read_json(path)
    verify_document(
        document,
        signing_key=signing_key,
        expected_kind=expected_kind,
        deployment_id=deployment_id,
    )
    return document


def atomic_write_json(path, document):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.is_symlink():
        raise RuntimeControlError("control_directory_unsafe")
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{time.time_ns()}.partial"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    file_descriptor = os.open(temporary, flags, 0o600)
    try:
        data = canonical_bytes(document)
        with os.fdopen(file_descriptor, "wb", closefd=True) as stream:
            file_descriptor = -1
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        temporary.unlink(missing_ok=True)


def runtime_control_paths(control_root):
    runtime_dir = Path(control_root) / "runtime"
    activation_dir = Path(control_root) / "activation"
    return {
        "active": runtime_dir / "active.json",
        "previous": runtime_dir / "previous.json",
        "activation_dir": activation_dir,
        "intents": activation_dir / "intents",
        "acks": activation_dir / "acks",
        "results": activation_dir / "results",
        "lock": activation_dir / "activation.lock",
    }


def _contained_runtime_path(runtime_path, runtime_root):
    configured = Path(runtime_path)
    root = Path(runtime_root).resolve()
    if configured.is_symlink() or not configured.is_dir():
        raise RuntimeControlError("runtime_workspace_unsafe")
    resolved = configured.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise RuntimeControlError("runtime_workspace_outside_root") from exc
    return resolved


def validate_runtime_workspace(
    runtime_path,
    *,
    runtime_root,
    generation_id,
    manifest_digest,
    require_immutable=True,
):
    runtime = _contained_runtime_path(runtime_path, runtime_root)
    for directory, directories, files in os.walk(
        runtime, followlinks=False
    ):
        directory_path = Path(directory)
        directory_stat = os.stat(directory_path, follow_symlinks=False)
        if (
            not stat.S_ISDIR(directory_stat.st_mode)
            or (
                require_immutable
                and directory_stat.st_mode & 0o222
            )
        ):
            raise RuntimeControlError(
                "runtime_workspace_mutable"
                if stat.S_ISDIR(directory_stat.st_mode)
                else "runtime_workspace_unsafe"
            )
        for name in directories:
            candidate = directory_path / name
            if candidate.is_symlink():
                raise RuntimeControlError("runtime_workspace_unsafe")
        for name in files:
            candidate = directory_path / name
            if candidate.is_symlink():
                raise RuntimeControlError("runtime_workspace_unsafe")
            file_stat = os.stat(candidate, follow_symlinks=False)
            if (
                not stat.S_ISREG(file_stat.st_mode)
                or file_stat.st_nlink != 1
                or (
                    require_immutable
                    and file_stat.st_mode & 0o222
                )
            ):
                raise RuntimeControlError(
                    "runtime_workspace_mutable"
                    if stat.S_ISREG(file_stat.st_mode)
                    and file_stat.st_nlink == 1
                    else "runtime_workspace_unsafe"
                )
    evidence_path = runtime / RUNTIME_EVIDENCE_FILE
    evidence = _read_json(evidence_path)
    if (
        evidence.get("generation_id") != generation_id
        or evidence.get("manifest_digest") != manifest_digest
    ):
        raise RuntimeControlError("runtime_evidence_identity_mismatch")
    database_path = runtime / "db.sqlite3"
    if database_path.is_symlink() or not database_path.is_file():
        raise RuntimeControlError("runtime_database_missing")
    database_stat = os.stat(database_path, follow_symlinks=False)
    if not stat.S_ISREG(database_stat.st_mode) or database_stat.st_nlink != 1:
        raise RuntimeControlError("runtime_database_unsafe")
    directories = {}
    for name, relative in RUNTIME_DIRECTORIES.items():
        candidate = runtime / relative
        if candidate.is_symlink() or not candidate.is_dir():
            raise RuntimeControlError(f"runtime_{name}_missing")
        directories[name] = candidate
    return runtime, database_path, directories, evidence


def set_runtime_workspace_writable(
    runtime_path,
    *,
    runtime_root,
    generation_id,
    manifest_digest,
    writable,
):
    """Thaw or freeze a structurally verified runtime at a process boundary."""
    runtime, _, _, _ = validate_runtime_workspace(
        runtime_path,
        runtime_root=runtime_root,
        generation_id=generation_id,
        manifest_digest=manifest_digest,
        require_immutable=False,
    )
    directory_mode = 0o750 if writable else 0o550
    file_mode = 0o640 if writable else 0o440
    for directory, directories, files in os.walk(
        runtime, topdown=False, followlinks=False
    ):
        directory_path = Path(directory)
        for name in files:
            os.chmod(
                directory_path / name,
                file_mode,
                follow_symlinks=False,
            )
        for name in directories:
            os.chmod(
                directory_path / name,
                directory_mode,
                follow_symlinks=False,
            )
        os.chmod(
            directory_path,
            directory_mode,
            follow_symlinks=False,
        )
    return validate_runtime_workspace(
        runtime,
        runtime_root=runtime_root,
        generation_id=generation_id,
        manifest_digest=manifest_digest,
        require_immutable=not writable,
    )[0]


def build_runtime_pointer(
    *,
    deployment_id,
    generation_id,
    manifest_digest,
    runtime_path,
    intent_digest,
    state_version,
    signing_key,
):
    payload = {
        "schema_version": 1,
        "kind": "runtime_pointer",
        "deployment_id": deployment_id,
        "generation_id": generation_id,
        "manifest_digest": manifest_digest,
        "runtime_path": str(Path(runtime_path).resolve()),
        "intent_digest": intent_digest,
        "state_version": int(state_version),
        "observed_at_unix": int(time.time()),
    }
    return sign_document(payload, signing_key)


def read_runtime_pointer(
    path,
    *,
    deployment_id,
    signing_key,
    runtime_root,
):
    document = _read_json(path)
    pointer_digest = verify_document(
        document,
        signing_key=signing_key,
        expected_kind="runtime_pointer",
        deployment_id=deployment_id,
    )
    generation_id = document.get("generation_id", "")
    manifest_digest = document.get("manifest_digest", "")
    if (
        not generation_id
        or len(manifest_digest) != 64
        or not document.get("intent_digest")
    ):
        raise RuntimeControlError("runtime_pointer_invalid")
    runtime, database, directories, _ = validate_runtime_workspace(
        document.get("runtime_path", ""),
        runtime_root=runtime_root,
        generation_id=generation_id,
        manifest_digest=manifest_digest,
        require_immutable=False,
    )
    return ResolvedRuntime(
        generation_id=generation_id,
        manifest_digest=manifest_digest,
        runtime_path=runtime,
        database_path=database,
        media_root=directories["media"],
        pdf_cache_dir=directories["pdf_cache"],
        faiss_index_dir=directories["faiss_indexes"],
        chroma_dir=directories["chroma_db"],
        pointer_digest=pointer_digest,
        intent_digest=document["intent_digest"],
    )


def resolve_runtime_from_env(
    environ: Mapping[str, str] | None = None,
):
    environment = os.environ if environ is None else environ
    enabled = str(
        environment.get("STAGING_RUNTIME_ACTIVATION_ENABLED", "0")
    ).strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return None
    if environment.get("APP_ENV", "").strip().lower() == "production":
        raise RuntimeControlError("production_activation_disabled")
    if environment.get("APP_ENV", "").strip().lower() != "staging":
        raise RuntimeControlError("staging_activation_environment_required")
    control_root = environment.get(
        "DATA_CONTROL_ROOT", "/app/data-control"
    )
    runtime_root = environment.get(
        "RUNTIME_GENERATIONS_ROOT",
        "/app/data/runtime-generations",
    )
    deployment_id = environment.get("DEPLOYMENT_ID", "").strip()
    signing_key = environment.get(
        "ACTIVATION_INTENT_SIGNING_KEY", ""
    )
    if not deployment_id:
        raise RuntimeControlError("activation_deployment_id_required")
    paths = runtime_control_paths(control_root)
    initial_enabled = str(
        environment.get("STAGING_INITIAL_ACTIVATION_ENABLED", "0")
    ).strip().lower() in {"1", "true", "yes", "on"}
    if (
        initial_enabled
        and not paths["active"].exists()
        and not paths["previous"].exists()
    ):
        # A fresh, explicitly opted-in staging target must remain able to
        # serve its restored control database long enough to schedule the
        # signed first activation. Any existing pointer, including an invalid
        # one, continues through strict verification below.
        return None
    return read_runtime_pointer(
        paths["active"],
        deployment_id=deployment_id,
        signing_key=signing_key,
        runtime_root=runtime_root,
    )
