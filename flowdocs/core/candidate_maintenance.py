"""Isolated execution and validation for derived maintenance candidates."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import uuid
import fcntl
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .artifact_cleanup import capacity_report
from .media_quarantine import (
    MediaFileAbsentError,
    MediaFileUnsafeError,
    build_unavailable_attestation,
    storage_key_evidence,
    validate_unavailable_attestation,
    verify_local_media_file,
)
from .models import MaintenanceAuditEvent, MaintenanceJob, PDFFile

WORKSPACE_MANIFEST = "maintenance-candidate.json"


class CandidateMaintenanceError(RuntimeError):
    def __init__(self, reason_code: str, detail: str = ""):
        self.reason_code = reason_code
        super().__init__(detail or reason_code)


class PinnedWorkspace:
    """A candidate directory retained by descriptor until publication handoff."""

    def __init__(self, path, root_descriptor, descriptor, root_authority):
        self.path = Path(path)
        self.root_descriptor = root_descriptor
        self.descriptor = descriptor
        self.root_authority = root_authority
        opened = os.fstat(descriptor)
        self.authority = (opened.st_dev, opened.st_ino)
        self.closed = False

    @property
    def name(self):
        return self.path.name

    def anchored_path(self):
        opened = os.fstat(self.descriptor)
        if (opened.st_dev, opened.st_ino) != self.authority:
            raise CandidateMaintenanceError(
                "maintenance_workspace_authority_changed"
            )
        return _descriptor_path(self.descriptor)

    def assert_lexical_identity(self):
        if self.path.is_symlink() or not self.path.is_dir():
            raise CandidateMaintenanceError(
                "maintenance_workspace_authority_changed"
            )
        observed = self.path.stat()
        if (observed.st_dev, observed.st_ino) != self.authority:
            raise CandidateMaintenanceError(
                "maintenance_workspace_authority_changed"
            )

    def close(self):
        if not self.closed:
            os.close(self.descriptor)
            os.close(self.root_descriptor)
            self.closed = True

    def __fspath__(self):
        return os.fspath(self.path)

    def __str__(self):
        return str(self.path)

    def __truediv__(self, value):
        return self.path / value

    def __del__(self):
        try:
            self.close()
        except OSError:
            pass


def workspace_root() -> Path:
    configured = getattr(
        settings,
        "MAINTENANCE_WORKSPACE_ROOT",
        Path(settings.DATA_CONTROL_ROOT) / "maintenance-workspaces",
    )
    # Preserve the configured lexical path. Resolving here would erase a
    # symlink before the authority check below could reject it.
    return Path(os.path.abspath(os.fspath(configured)))


def _workspace_root_authority(*, create: bool = False) -> tuple[Path, tuple[int, int]]:
    """Validate a lexical workspace path and bind its existing parent."""
    root = workspace_root()
    current = Path(root.anchor)
    for part in root.parts[1:]:
        candidate = current / part
        if candidate.is_symlink():
            raise CandidateMaintenanceError("maintenance_workspace_unsafe")
        if not candidate.exists():
            break
        current = candidate
    if (
        not current.is_dir()
        or current.is_symlink()
        or not current.stat().st_mode & 0o222
    ):
        raise CandidateMaintenanceError("maintenance_workspace_unwritable")
    parent_stat = current.stat()
    authority = (parent_stat.st_dev, parent_stat.st_ino)
    if create:
        allowed_parent = current
        root.mkdir(parents=True, exist_ok=True)
        # Repeat every lexical-component check after mkdir. This detects a
        # parent swap instead of trusting the path walk that preceded it.
        verified_root, verified_authority = _workspace_root_authority()
        allowed_parent_stat = allowed_parent.stat()
        if (
            verified_root != root
            or (allowed_parent_stat.st_dev, allowed_parent_stat.st_ino)
            != authority
        ):
            raise CandidateMaintenanceError("maintenance_workspace_authority_changed")
        if not root.is_dir() or root.is_symlink():
            raise CandidateMaintenanceError("maintenance_workspace_unsafe")
        return verified_root, verified_authority
    return root, authority


def _open_workspace_root(root: Path, authority: tuple[int, int]) -> int:
    """Open and pin the authorized root without following its final path."""
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(root, flags)
    except OSError as exc:
        raise CandidateMaintenanceError("maintenance_workspace_unsafe") from exc
    opened = os.fstat(descriptor)
    if (opened.st_dev, opened.st_ino) != authority:
        os.close(descriptor)
        raise CandidateMaintenanceError("maintenance_workspace_authority_changed")
    return descriptor


def _descriptor_path(descriptor: int) -> Path:
    proc = Path("/proc/self/fd") / str(descriptor)
    if proc.exists():
        return proc
    if hasattr(fcntl, "F_GETPATH"):
        raw = fcntl.fcntl(descriptor, fcntl.F_GETPATH, b"\0" * 1024)
        return Path(raw.split(b"\0", 1)[0].decode())
    raise CandidateMaintenanceError("maintenance_workspace_unsafe")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _copy_sqlite(source: Path, target: Path) -> None:
    source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    target_connection = sqlite3.connect(target)
    try:
        source_connection.backup(target_connection)
    finally:
        target_connection.close()
        source_connection.close()


def _copy_tree(source: Path, target: Path) -> None:
    """Copy a tree without ever following a source-side symlink."""
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | no_follow

    def copy_directory(source_fd: int, target_fd: int) -> None:
        for name in sorted(os.listdir(source_fd)):
            try:
                before = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
                if stat.S_ISLNK(before.st_mode):
                    raise CandidateMaintenanceError(
                        "maintenance_source_runtime_unsafe"
                    )
                if stat.S_ISDIR(before.st_mode):
                    os.mkdir(name, mode=0o700, dir_fd=target_fd)
                    child_source = os.open(name, directory_flags, dir_fd=source_fd)
                    child_target = os.open(name, directory_flags, dir_fd=target_fd)
                    try:
                        opened = os.fstat(child_source)
                        if (opened.st_dev, opened.st_ino) != (
                            before.st_dev,
                            before.st_ino,
                        ):
                            raise CandidateMaintenanceError(
                                "maintenance_source_snapshot_changed"
                            )
                        copy_directory(child_source, child_target)
                    finally:
                        os.close(child_target)
                        os.close(child_source)
                    continue
                if not stat.S_ISREG(before.st_mode):
                    raise CandidateMaintenanceError(
                        "maintenance_source_runtime_unsafe"
                    )
                source_file = os.open(
                    name, os.O_RDONLY | no_follow, dir_fd=source_fd
                )
                try:
                    opened = os.fstat(source_file)
                    if (opened.st_dev, opened.st_ino) != (
                        before.st_dev,
                        before.st_ino,
                    ):
                        raise CandidateMaintenanceError(
                            "maintenance_source_snapshot_changed"
                        )
                    target_file = os.open(
                        name,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | no_follow,
                        0o600,
                        dir_fd=target_fd,
                    )
                    try:
                        while True:
                            block = os.read(source_file, 1024 * 1024)
                            if not block:
                                break
                            remaining = memoryview(block)
                            while remaining:
                                written = os.write(target_file, remaining)
                                remaining = remaining[written:]
                    finally:
                        os.close(target_file)
                finally:
                    os.close(source_file)
            except CandidateMaintenanceError:
                raise
            except OSError as exc:
                raise CandidateMaintenanceError(
                    "maintenance_source_runtime_unsafe"
                ) from exc

    if source.is_symlink():
        raise CandidateMaintenanceError("maintenance_source_runtime_unsafe")
    if not source.exists():
        target.mkdir(parents=True)
        return
    source_fd = None
    target_fd = None
    try:
        source_fd = os.open(source, directory_flags)
        target.mkdir(mode=0o700)
        target_fd = os.open(target, directory_flags)
    except OSError as exc:
        if target_fd is not None:
            os.close(target_fd)
        if source_fd is not None:
            os.close(source_fd)
        raise CandidateMaintenanceError(
            "maintenance_source_runtime_unsafe"
        ) from exc
    try:
        copy_directory(source_fd, target_fd)
    finally:
        os.close(target_fd)
        os.close(source_fd)
    _tree_identity(target)


def _tree_identity(root: Path) -> dict:
    """Return a content identity without exposing document names."""
    digest = hashlib.sha256()
    file_count = 0
    byte_count = 0
    if not root.is_dir() or root.is_symlink():
        raise CandidateMaintenanceError("maintenance_source_runtime_unsafe")
    for path in sorted(root.rglob("*"), key=lambda value: value.as_posix()):
        if path.is_symlink():
            raise CandidateMaintenanceError("maintenance_source_runtime_unsafe")
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            digest.update(f"d\\0{relative}\\0".encode())
            continue
        if not path.is_file():
            raise CandidateMaintenanceError("maintenance_source_runtime_unsafe")
        size = path.stat().st_size
        file_hash = _sha256(path)
        digest.update(
            f"f\\0{relative}\\0{size}\\0{file_hash}\\0".encode()
        )
        file_count += 1
        byte_count += size
    return {
        "sha256": digest.hexdigest(),
        "files": file_count,
        "bytes": byte_count,
    }


def _tree_identity_fd(root_fd: int) -> dict:
    """Identify a pinned directory without trusting an fd-symlink pathname."""
    digest = hashlib.sha256()
    file_count = 0
    byte_count = 0
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | no_follow

    def walk(directory_fd: int, prefix: str = "") -> None:
        nonlocal file_count, byte_count
        for name in sorted(os.listdir(directory_fd)):
            relative = f"{prefix}/{name}" if prefix else name
            try:
                before = os.stat(
                    name, dir_fd=directory_fd, follow_symlinks=False
                )
                if stat.S_ISLNK(before.st_mode):
                    raise CandidateMaintenanceError(
                        "maintenance_source_runtime_unsafe"
                    )
                if stat.S_ISDIR(before.st_mode):
                    digest.update(f"d\0{relative}\0".encode())
                    child = os.open(name, directory_flags, dir_fd=directory_fd)
                    try:
                        opened = os.fstat(child)
                        if (opened.st_dev, opened.st_ino) != (
                            before.st_dev,
                            before.st_ino,
                        ):
                            raise CandidateMaintenanceError(
                                "maintenance_source_snapshot_changed"
                            )
                        walk(child, relative)
                    finally:
                        os.close(child)
                    continue
                if not stat.S_ISREG(before.st_mode):
                    raise CandidateMaintenanceError(
                        "maintenance_source_runtime_unsafe"
                    )
                source = os.open(
                    name, os.O_RDONLY | no_follow, dir_fd=directory_fd
                )
                try:
                    opened = os.fstat(source)
                    if (opened.st_dev, opened.st_ino) != (
                        before.st_dev,
                        before.st_ino,
                    ):
                        raise CandidateMaintenanceError(
                            "maintenance_source_snapshot_changed"
                        )
                    file_digest = hashlib.sha256()
                    while True:
                        block = os.read(source, 1024 * 1024)
                        if not block:
                            break
                        file_digest.update(block)
                finally:
                    os.close(source)
                digest.update(
                    f"f\0{relative}\0{before.st_size}\0"
                    f"{file_digest.hexdigest()}\0".encode()
                )
                file_count += 1
                byte_count += before.st_size
            except CandidateMaintenanceError:
                raise
            except OSError as exc:
                raise CandidateMaintenanceError(
                    "maintenance_source_runtime_unsafe"
                ) from exc

    walk(root_fd)
    return {
        "sha256": digest.hexdigest(),
        "files": file_count,
        "bytes": byte_count,
    }


def _mutable_source_identities() -> dict:
    return {
        "media": _tree_identity(Path(settings.MEDIA_ROOT)),
        "faiss": _tree_identity(Path(settings.FAISS_INDEX_DIR)),
        "chroma": _tree_identity(Path(settings.CHROMA_DIR)),
    }


def _source_identity(
    job: MaintenanceJob,
    parent,
    *,
    snapshot_database: Path,
    mutation_epoch: int,
    source_trees: dict,
) -> dict:
    return {
        "job_id": str(job.public_id),
        "database_sha256": _sha256(snapshot_database),
        "mutation_epoch": mutation_epoch,
        "source_trees": source_trees,
        "runtime_generation_id": parent.generation_id,
        "runtime_manifest_digest": parent.manifest_digest,
        "runtime_pointer_digest": parent.pointer_digest,
        "parent_tree": _tree_identity(Path(parent.runtime_path)),
        "recovery_set_id": job.options.get("recovery_set_id", ""),
    }


def _verified_mutable_source_runtime_identity():
    """Bind a mutable writer snapshot to the verified signed runtime parent."""
    from vaultops.models import (
        ArtifactGeneration,
        RuntimePointerObservation,
    )
    from vaultops.runtime_control import (
        RuntimeControlError,
        read_runtime_pointer,
        runtime_control_paths,
    )

    paths = runtime_control_paths(settings.DATA_CONTROL_ROOT)
    try:
        pointer = read_runtime_pointer(
            paths["active"],
            deployment_id=settings.ENV_IDENTITY.deployment_id,
            signing_key=settings.ACTIVATION_INTENT_SIGNING_KEY,
            runtime_root=settings.RUNTIME_GENERATIONS_ROOT,
        )
    except (RuntimeControlError, OSError) as exc:
        raise CandidateMaintenanceError(
            "maintenance_source_pointer_unverified"
        ) from exc
    generation = ArtifactGeneration.objects.using("control").filter(
        deployment_id=settings.ENV_IDENTITY.deployment_id,
        generation_id=pointer.generation_id,
        manifest_digest=pointer.manifest_digest,
        runtime_state=ArtifactGeneration.RuntimeState.ACTIVE,
    ).first()
    if generation is None:
        raise CandidateMaintenanceError(
            "maintenance_source_generation_unprojected"
        )
    observation = (
        RuntimePointerObservation.objects.using("control")
        .filter(deployment_id=settings.ENV_IDENTITY.deployment_id)
        .order_by("-observed_at")
        .first()
    )
    if (
        observation is None
        or observation.active_generation_id != pointer.generation_id
        or observation.pointer_digest != pointer.pointer_digest
        or observation.status != "committed"
        or observation.readiness_evidence.get("livez") != "ok"
        or observation.readiness_evidence.get("readyz") != "ready"
        or observation.readiness_evidence.get("runtime_generation_id")
        != pointer.generation_id
        or observation.readiness_evidence.get("runtime_manifest_digest")
        != pointer.manifest_digest
        or observation.readiness_evidence.get("runtime_smoke") != "passed"
    ):
        raise CandidateMaintenanceError(
            "maintenance_source_observation_stale"
        )
    return pointer


def _verified_mutable_source_runtime():
    """Return the verified parent pointer bound to this process' data paths.

    Maintenance always copies this parent into an isolated candidate before
    running a job.  Exact path binding prevents a correctly signed pointer
    from authorizing work against bootstrap or otherwise mismatched paths.
    """
    from vaultops.runtime_control import read_runtime_pointer, runtime_control_paths

    verified_pointer = _verified_mutable_source_runtime_identity()
    pointer = read_runtime_pointer(
        runtime_control_paths(settings.DATA_CONTROL_ROOT)["active"],
        deployment_id=settings.ENV_IDENTITY.deployment_id,
        signing_key=settings.ACTIVATION_INTENT_SIGNING_KEY,
        runtime_root=settings.RUNTIME_GENERATIONS_ROOT,
    )
    if (
        pointer.generation_id != verified_pointer.generation_id
        or pointer.manifest_digest != verified_pointer.manifest_digest
        or pointer.pointer_digest != verified_pointer.pointer_digest
    ):
        raise CandidateMaintenanceError(
            "maintenance_source_authority_changed"
        )
    configured_paths = {
        "database": Path(settings.DATABASES["default"]["NAME"]).resolve(),
        "media": Path(settings.MEDIA_ROOT).resolve(),
        "faiss": Path(settings.FAISS_INDEX_DIR).resolve(),
        "chroma": Path(settings.CHROMA_DIR).resolve(),
    }
    pointer_paths = {
        "database": pointer.database_path.resolve(),
        "media": pointer.media_root.resolve(),
        "faiss": pointer.faiss_index_dir.resolve(),
        "chroma": pointer.chroma_dir.resolve(),
    }
    if configured_paths != pointer_paths:
        raise CandidateMaintenanceError(
            "maintenance_source_runtime_identity_mismatch"
        )
    return pointer


def _maintenance_source_parent():
    if (
        getattr(settings, "ACTIVE_RUNTIME", None) is not None
        and not settings.MAINTENANCE_CANDIDATE_PREPARATION_ENABLED
    ):
        raise CandidateMaintenanceError(
            "maintenance_candidate_preparation_disabled"
        )
    if settings.MAINTENANCE_CANDIDATE_PREPARATION_ENABLED:
        return _verified_mutable_source_runtime()
    generation_id = getattr(settings, "RUNTIME_GENERATION_ID", "")
    manifest_digest = getattr(settings, "RUNTIME_MANIFEST_DIGEST", "")
    if not generation_id or not manifest_digest:
        raise CandidateMaintenanceError("maintenance_source_pointer_unverified")
    return SimpleNamespace(
        generation_id=generation_id,
        manifest_digest=manifest_digest,
        pointer_digest=hashlib.sha256(
            f"{generation_id}:{manifest_digest}".encode()
        ).hexdigest(),
        runtime_path=str(Path(settings.DATA_ROOT).resolve()),
    )


def maintenance_source_capability_reason() -> str:
    """Return a bounded reason when a mutation source cannot be verified.

    Candidate execution must fail closed when its parent runtime is not
    authoritative.  Expose the same read-only preflight to presentation and
    queue gates so operators are not invited to start work that the worker
    will deterministically reject.
    """
    try:
        _maintenance_source_parent()
        _workspace_root_authority()
    except CandidateMaintenanceError as exc:
        return exc.reason_code
    except Exception:
        return "maintenance_source_pointer_unverified"
    return ""


def estimate_workspace_bytes(job: MaintenanceJob) -> int:
    database = Path(settings.DATABASES["default"]["NAME"])
    roots = [
        Path(settings.MEDIA_ROOT),
        Path(settings.FAISS_INDEX_DIR),
        Path(settings.CHROMA_DIR),
    ]
    source_bytes = database.stat().st_size if database.is_file() else 0
    for root in roots:
        if root.is_dir():
            source_bytes += sum(
                path.stat().st_size for path in root.rglob("*") if path.is_file()
            )
    return source_bytes


def create_workspace(job: MaintenanceJob) -> Path:
    root, root_authority = _workspace_root_authority(create=True)
    source_bytes = estimate_workspace_bytes(job)
    capacity = capacity_report(source_bytes=source_bytes, operation="maintenance")
    if not capacity["byte_capacity_ok"] or not capacity["inode_capacity_ok"]:
        raise CandidateMaintenanceError("insufficient_maintenance_capacity")
    root_descriptor = _open_workspace_root(root, root_authority)
    workspace = root / f"mw-{job.public_id}-{uuid.uuid4().hex[:8]}"
    temporary_name = f".{workspace.name}.tmp"
    try:
        os.mkdir(temporary_name, mode=0o700, dir_fd=root_descriptor)
        temporary_descriptor = os.open(
            temporary_name,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=root_descriptor,
        )
    except Exception:
        shutil.rmtree(
            _descriptor_path(root_descriptor) / temporary_name,
            ignore_errors=True,
        )
        os.close(root_descriptor)
        raise
    temporary = _descriptor_path(temporary_descriptor)
    barrier_acquired = False
    transferred = False
    try:
        from vaultops.services.mutations import (
            assert_barrier_owner,
            release_barrier,
            request_barrier,
        )

        deployment_id = settings.ENV_IDENTITY.deployment_id
        barrier = request_barrier(
            owner_job_id=job.public_id,
            source_deployment=deployment_id,
        )
        barrier_acquired = True
        barrier = assert_barrier_owner(
            owner_job_id=job.public_id,
            source_deployment=deployment_id,
        )
        mutation_epoch = barrier.current_epoch
        parent = _maintenance_source_parent()
        parent_tree_before = _tree_identity(Path(parent.runtime_path))
        live_database_configured = Path(settings.DATABASES["default"]["NAME"])
        if live_database_configured.is_symlink():
            raise CandidateMaintenanceError(
                "maintenance_source_runtime_unsafe"
            )
        live_database = live_database_configured.resolve()
        live_database_before = _sha256(live_database)
        source_trees_before = _mutable_source_identities()
        _copy_sqlite(live_database, temporary / "db.sqlite3")
        _copy_tree(Path(settings.MEDIA_ROOT), temporary / "media")
        _copy_tree(
            Path(settings.FAISS_INDEX_DIR),
            temporary / "faiss_indexes",
        )
        _copy_tree(
            Path(settings.CHROMA_DIR), temporary / "chroma_db"
        )
        _tree_identity_fd(temporary_descriptor)
        (temporary / "pdf_cache").mkdir()
        (temporary / "backups").mkdir()
        parent_after = _maintenance_source_parent()
        parent_tree_after = _tree_identity(Path(parent_after.runtime_path))
        source_trees_after = _mutable_source_identities()
        live_database_after = _sha256(live_database)
        barrier_after = assert_barrier_owner(
            owner_job_id=job.public_id,
            source_deployment=deployment_id,
        )
        source = _source_identity(
            job,
            parent,
            snapshot_database=temporary / "db.sqlite3",
            mutation_epoch=mutation_epoch,
            source_trees=source_trees_before,
        )
        if (
            parent_after.pointer_digest != parent.pointer_digest
            or parent_tree_after != parent_tree_before
            or source["parent_tree"] != parent_tree_before
        ):
            raise CandidateMaintenanceError(
                "maintenance_source_authority_changed"
            )
        if (
            barrier_after.current_epoch != mutation_epoch
            or live_database_after != live_database_before
            or source_trees_after != source_trees_before
        ):
            raise CandidateMaintenanceError(
                "maintenance_source_snapshot_changed"
            )
        source["snapshot"] = _tree_identity_fd(temporary_descriptor)
        unavailable_documents = _database_unavailable_attestation(
            temporary / "db.sqlite3"
        )
        (temporary / WORKSPACE_MANIFEST).write_text(
            json.dumps(
                {
                    "manifest_version": 1,
                    "workspace_id": workspace.name,
                    "state": "prepared",
                    "created_at": timezone.now().isoformat(),
                    "expires_at": (
                        timezone.now() + timedelta(days=7)
                    ).isoformat(),
                    "source": source,
                    "operation": job.kind,
                    "unavailable_documents": unavailable_documents,
                    "capacity": capacity,
                    "selected_pdf_ids": list(
                        job.items.exclude(pdf_id=None).values_list(
                            "pdf_id", flat=True
                        )
                    ),
                    "affected_folder_ids": sorted(
                        set(
                            job.items.exclude(folder_id=None).values_list(
                                "folder_id", flat=True
                            )
                        )
                    ),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        checked_root, checked_authority = _workspace_root_authority()
        opened_root = os.fstat(root_descriptor)
        if (
            checked_root != root
            or checked_authority != root_authority
            or (opened_root.st_dev, opened_root.st_ino) != root_authority
        ):
            raise CandidateMaintenanceError(
                "maintenance_workspace_authority_changed"
            )
        os.rename(
            temporary_name,
            workspace.name,
            src_dir_fd=root_descriptor,
            dst_dir_fd=root_descriptor,
        )
        pinned = PinnedWorkspace(
            workspace,
            root_descriptor,
            temporary_descriptor,
            root_authority,
        )
        pinned.assert_lexical_identity()
        transferred = True
        return pinned
    except Exception:
        shutil.rmtree(
            _descriptor_path(root_descriptor) / temporary_name,
            ignore_errors=True,
        )
        raise
    finally:
        if barrier_acquired:
            release_barrier(
                owner_job_id=job.public_id,
                source_deployment=settings.ENV_IDENTITY.deployment_id,
                tolerate_lost=True,
            )
        if not transferred:
            os.close(temporary_descriptor)
            os.close(root_descriptor)


def _candidate_environment(workspace: Path) -> dict:
    environment = os.environ.copy()
    environment.update(
        {
            "DJANGO_SETTINGS_MODULE": "flowdocs.settings_candidate",
            "MAINTENANCE_WORKSPACE_ROOT": str(workspace),
            "MAINTENANCE_CANDIDATE_EXECUTION": "1",
            "MAINTENANCE_CANDIDATE_PREPARATION_ENABLED": "0",
            "VAULT_SYNC_ENABLED": "0",
            "STAGING_RUNTIME_ACTIVATION_ENABLED": "0",
        }
    )
    return environment


def _database_unavailable_attestation(database: Path) -> dict:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(core_pdffile)")
        }
        if "lifecycle" not in columns:
            return build_unavailable_attestation(())
        expected_sha_column = (
            "media_expected_sha256"
            if "media_expected_sha256" in columns
            else "''"
        )
        expected_size_column = (
            "media_expected_size"
            if "media_expected_size" in columns
            else "NULL"
        )
        prior_lifecycle_column = (
            "media_prior_lifecycle"
            if "media_prior_lifecycle" in columns
            else "''"
        )
        rows = connection.execute(
            f"SELECT id, lifecycle, file, {expected_sha_column}, "
            f"{expected_size_column}, {prior_lifecycle_column} "
            "FROM core_pdffile WHERE lifecycle = 'unavailable' ORDER BY id"
        )
        return build_unavailable_attestation(
            (
                {
                    "id": row[0],
                    "lifecycle": row[1],
                    "storage_key_status": storage_key_evidence(row[2])["status"],
                    "storage_key_token_sha256": storage_key_evidence(row[2])[
                        "token_sha256"
                    ],
                    "expected_sha256": row[3],
                    "expected_size": row[4],
                    "prior_lifecycle": row[5],
                }
                for row in rows
            )
        )
    finally:
        connection.close()


def _embedding_validation(database: Path) -> dict:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT id, folder_id, page_chunks, chunk_embeddings FROM core_pdffile "
            "WHERE lifecycle IN ('uploaded', 'processing', 'ready')",
        )
        dimensions = set()
        vectors = 0
        folders = {}
        for pdf_id, folder_id, chunks_raw, embeddings_raw in rows:
            if not isinstance(folder_id, int) or folder_id <= 0:
                raise CandidateMaintenanceError(
                    "candidate_folder_invalid", str(pdf_id)
                )
            try:
                chunks = json.loads(chunks_raw or "[]")
                embeddings = json.loads(embeddings_raw or "[]")
            except (TypeError, ValueError) as exc:
                raise CandidateMaintenanceError(
                    "candidate_embedding_metadata_invalid", str(pdf_id)
                ) from exc
            if not isinstance(chunks, list) or not isinstance(embeddings, list):
                raise CandidateMaintenanceError(
                    "candidate_embedding_metadata_invalid", str(pdf_id)
                )
            if len(chunks) != len(embeddings):
                raise CandidateMaintenanceError(
                    "candidate_embedding_count_mismatch", str(pdf_id)
                )
            for embedding in embeddings:
                if (
                    not isinstance(embedding, list)
                    or not embedding
                    or any(
                        isinstance(value, bool)
                        or not isinstance(value, (int, float))
                        or not math.isfinite(value)
                        for value in embedding
                    )
                ):
                    raise CandidateMaintenanceError(
                        "candidate_embedding_invalid", str(pdf_id)
                    )
                dimensions.add(len(embedding))
                vectors += 1
            folder = folders.setdefault(
                str(folder_id), {"vectors": 0, "dimensions": set()}
            )
            folder["vectors"] += len(embeddings)
            folder["dimensions"].update(len(embedding) for embedding in embeddings)
        if len(dimensions) > 1:
            raise CandidateMaintenanceError(
                "candidate_embedding_dimension_mismatch"
            )
        return {
            "vectors": vectors,
            "dimensions": sorted(dimensions),
            "folders": {
                folder_id: {
                    "vectors": record["vectors"],
                    "dimensions": sorted(record["dimensions"]),
                }
                for folder_id, record in sorted(folders.items())
            },
        }
    finally:
        connection.close()


def validate_candidate(workspace: Path) -> dict:
    manifest_path = workspace / WORKSPACE_MANIFEST
    manifest = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError) as exc:
            raise CandidateMaintenanceError(
                "candidate_manifest_invalid"
            ) from exc
    try:
        affected_folder_ids = sorted(
            {
                int(folder_id)
                for folder_id in manifest.get("affected_folder_ids", [])
            }
        )
    except (TypeError, ValueError) as exc:
        raise CandidateMaintenanceError("candidate_manifest_invalid") from exc
    database = workspace / "db.sqlite3"
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = list(connection.execute("PRAGMA foreign_key_check"))
        lifecycle_rows = list(
            connection.execute(
                "SELECT id, file, lifecycle FROM core_pdffile ORDER BY id"
            )
        )
    finally:
        connection.close()
    if integrity != "ok":
        raise CandidateMaintenanceError("candidate_sqlite_integrity_failed")
    if foreign_keys:
        raise CandidateMaintenanceError("candidate_foreign_keys_failed")
    valid_lifecycles = {
        "uploaded",
        "processing",
        "ready",
        "deprecated",
        "archived",
        "unavailable",
    }
    if any(row[2] not in valid_lifecycles for row in lifecycle_rows):
        raise CandidateMaintenanceError("candidate_lifecycle_invalid")
    media_rows = [
        (pdf_id, value)
        for pdf_id, value, lifecycle in lifecycle_rows
        if lifecycle != "unavailable"
    ]
    unavailable_attestation = _database_unavailable_attestation(database)
    try:
        expected_unavailable = validate_unavailable_attestation(
            manifest.get(
                "unavailable_documents",
                build_unavailable_attestation(()),
            )
        )
    except ValueError as exc:
        raise CandidateMaintenanceError(
            "candidate_unavailable_attestation_invalid"
        ) from exc
    if unavailable_attestation != expected_unavailable:
        raise CandidateMaintenanceError(
            "candidate_unavailable_attestation_changed"
        )
    media_root = workspace / "media"
    missing_media = []
    for pdf_id, value in media_rows:
        try:
            verify_local_media_file(
                media_root,
                value,
                maximum_bytes=settings.ARTIFACT_INVENTORY_MAX_MEDIA_FILE_BYTES,
                hash_content=False,
            )
        except (MediaFileAbsentError, MediaFileUnsafeError):
            missing_media.append(pdf_id)
    if missing_media:
        raise CandidateMaintenanceError(
            "candidate_media_missing", str(len(missing_media))
        )
    embedding = _embedding_validation(database)
    validated_folder_ids = sorted(
        int(folder_id)
        for folder_id, record in embedding["folders"].items()
        if record["vectors"]
    )
    faiss_records = {}
    try:
        import faiss as faiss_module

        for folder_id in validated_folder_ids:
            path = workspace / "faiss_indexes" / f"folder_{folder_id}.index"
            if not path.is_file():
                raise CandidateMaintenanceError(
                    "candidate_faiss_missing", str(folder_id)
                )
            try:
                index = faiss_module.read_index(str(path))
            except Exception as exc:
                raise CandidateMaintenanceError(
                    "candidate_faiss_invalid", str(folder_id)
                ) from exc
            faiss_records[str(folder_id)] = {
                "vectors": int(index.ntotal),
                "dimension": int(index.d),
                "sha256": _sha256(path),
            }
    except ImportError as exc:
        raise CandidateMaintenanceError("candidate_faiss_unavailable") from exc
    actual_folder_ids = set()
    index_root = workspace / "faiss_indexes"
    if not index_root.is_dir() or index_root.is_symlink():
        raise CandidateMaintenanceError("candidate_faiss_directory_invalid")
    for path in index_root.iterdir():
        match = re.fullmatch(r"folder_(\d+)\.index", path.name)
        if not match:
            continue
        if path.is_symlink() or not path.is_file():
            raise CandidateMaintenanceError("candidate_faiss_unsafe", path.name)
        actual_folder_ids.add(int(match.group(1)))
    unexpected = sorted(actual_folder_ids - set(validated_folder_ids))
    if unexpected:
        raise CandidateMaintenanceError(
            "candidate_faiss_orphaned", ",".join(map(str, unexpected))
        )
    dimensions = embedding["dimensions"]
    if dimensions and any(
        record["dimension"] != dimensions[0]
        for record in faiss_records.values()
    ):
        raise CandidateMaintenanceError("candidate_faiss_dimension_mismatch")
    for folder_id, record in faiss_records.items():
        expected = embedding["folders"].get(
            folder_id, {"vectors": 0, "dimensions": []}
        )
        if record["vectors"] != expected["vectors"]:
            raise CandidateMaintenanceError(
                "candidate_faiss_count_mismatch", folder_id
            )
        if (
            expected["dimensions"]
            and record["dimension"] != expected["dimensions"][0]
        ):
            raise CandidateMaintenanceError(
                "candidate_faiss_dimension_mismatch", folder_id
            )
    return {
        "sqlite": {"integrity": "ok", "foreign_key_violations": 0},
        "media": {
            "referenced": len(media_rows),
            "missing": 0,
            "unavailable": unavailable_attestation,
        },
        "embeddings": embedding,
        "faiss": faiss_records,
        "affected_folder_ids": affected_folder_ids,
        "validated_folder_ids": validated_folder_ids,
    }


def _mirror_job(candidate_db: Path, source_job: MaintenanceJob) -> None:
    connection = sqlite3.connect(f"file:{candidate_db}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        candidate = connection.execute(
            "SELECT status, completed_items, failed_items, error_summary, options, "
            "finished_at FROM core_maintenancejob WHERE id=?",
            (source_job.pk,),
        ).fetchone()
        items = connection.execute(
            "SELECT id, status, attempts, error_code, error_message, started_at, "
            "finished_at FROM core_maintenancejobitem WHERE job_id=?",
            (source_job.pk,),
        ).fetchall()
    finally:
        connection.close()
    if candidate is None:
        raise CandidateMaintenanceError("candidate_job_missing")
    with transaction.atomic():
        for item in items:
            source_job.items.filter(pk=item["id"]).update(
                status=item["status"],
                attempts=item["attempts"],
                error_code=item["error_code"],
                error_message=item["error_message"],
                started_at=parse_datetime(item["started_at"])
                if item["started_at"] else None,
                finished_at=parse_datetime(item["finished_at"])
                if item["finished_at"] else None,
            )
        source_job.status = candidate["status"]
        source_job.completed_items = candidate["completed_items"]
        source_job.failed_items = candidate["failed_items"]
        source_job.error_summary = candidate["error_summary"]
        source_job.finished_at = (
            parse_datetime(candidate["finished_at"])
            if candidate["finished_at"] else None
        )
        candidate_options = json.loads(candidate["options"] or "{}")
        cumulative_builds = {
            str(key): int(value)
            for key, value in source_job.options.get(
                "candidate_folder_build_attempts", {}
            ).items()
        }
        for key, value in candidate_options.get(
            "folder_build_attempts", {}
        ).items():
            cumulative_builds[str(key)] = (
                cumulative_builds.get(str(key), 0) + int(value)
            )
        source_job.options = {
            **source_job.options,
            "candidate_job_options": candidate_options,
            "candidate_folder_build_attempts": cumulative_builds,
        }
        source_job.save(
            update_fields=[
                "status", "completed_items", "failed_items", "error_summary",
                "finished_at", "options", "updated_at",
            ]
        )


def execute_candidate_job(job: MaintenanceJob) -> MaintenanceJob:
    if not job.options.get("recovery_set_id"):
        raise CandidateMaintenanceError("recovery_point_required")
    workspace = create_workspace(job)
    execution_workspace = workspace.anchored_path()
    manifest_path = execution_workspace / WORKSPACE_MANIFEST
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(Path(settings.BASE_DIR) / "manage.py"),
                "run_maintenance_candidate",
                "--job-id",
                str(job.public_id),
            ],
            cwd=settings.BASE_DIR,
            env=_candidate_environment(execution_workspace),
            pass_fds=(workspace.descriptor,),
            capture_output=True,
            text=True,
            timeout=getattr(settings, "MAINTENANCE_JOB_TIMEOUT_SECONDS", 7200),
            check=False,
        )
        execution_workspace = workspace.anchored_path()
        _mirror_job(execution_workspace / "db.sqlite3", job)
        job.refresh_from_db()
        if result.returncode or job.status != "completed":
            raise CandidateMaintenanceError(
                "candidate_execution_failed", f"exit={result.returncode}"
            )
        validation = validate_candidate(execution_workspace)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.update(
            {
                "state": "activation_ready",
                "completed_at": timezone.now().isoformat(),
                "validation": validation,
                "derived": {
                    "database_sha256": _sha256(
                        execution_workspace / "db.sqlite3"
                    ),
                    "manifest_basis_sha256": hashlib.sha256(
                        json.dumps(
                            validation, sort_keys=True
                        ).encode("utf-8")
                    ).hexdigest(),
                },
                "publication": {
                    "state": "required",
                    "vault_generation_stale": True,
                },
            }
        )
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        workspace.assert_lexical_identity()
        job.options = {
            **job.options,
            "candidate_workspace_id": workspace.name,
            "candidate_workspace": str(workspace),
            "candidate_workspace_identity": {
                "device": workspace.authority[0],
                "inode": workspace.authority[1],
            },
            "candidate_state": "activation_ready",
        }
        job.save(update_fields=["options", "updated_at"])
        MaintenanceAuditEvent.objects.create(
            job=job,
            event_type="completed",
            payload={
                "phase": "candidate_validation",
                "workspace_id": workspace.name,
                "state": "activation_ready",
                "vault_generation_stale": True,
            },
        )
        return job
    except Exception as exc:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.update(
            {
                "state": "failed",
                "failed_at": timezone.now().isoformat(),
                "safe_error_code": getattr(
                    exc, "reason_code", exc.__class__.__name__.lower()
                ),
            }
        )
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        job.refresh_from_db()
        job.status = "failed"
        job.error_summary = getattr(
            exc, "reason_code", "candidate_execution_failed"
        )
        job.finished_at = timezone.now()
        job.options = {
            **job.options,
            "candidate_workspace_id": workspace.name,
            "candidate_workspace": str(workspace),
            "candidate_state": "failed",
        }
        job.save(
            update_fields=[
                "status", "error_summary", "finished_at", "options", "updated_at"
            ]
        )
        return job
    finally:
        workspace.close()
