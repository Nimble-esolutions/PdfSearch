import hashlib
import json
import os
import secrets
import sqlite3
import stat
import time
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from core.management.commands.inventory_artifacts import build_manifest
from core.media_quarantine import (
    build_unavailable_attestation,
    storage_key_status,
)
from vaultops.models import SourceSnapshot, VaultJobStep
from vaultops.services.mutations import (
    ConsistentSnapshotUnproven,
    assert_barrier_owner,
    current_epoch,
    deployment_id,
    journal_since,
    release_barrier,
    request_barrier,
)


class SnapshotError(RuntimeError):
    reason_code = "consistent_snapshot_unproven"

    def __init__(self, reason_code=None):
        self.reason_code = reason_code or self.reason_code
        super().__init__(self.reason_code)


class SnapshotCancelled(SnapshotError):
    reason_code = "snapshot_cancelled"


def _sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stat_signature(path):
    value = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(value.st_mode):
        raise SnapshotError("snapshot_source_not_regular")
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
    )


def _safe_files(root):
    root = root.resolve()
    if not root.exists():
        return []
    if not root.is_dir() or root.is_symlink():
        raise SnapshotError("snapshot_source_root_unsafe")
    results = []
    for directory, directories, files in os.walk(root, followlinks=False):
        directories.sort()
        files.sort()
        directory_path = Path(directory)
        for name in tuple(directories):
            candidate = directory_path / name
            if candidate.is_symlink():
                raise SnapshotError("snapshot_source_symlink_rejected")
        for name in files:
            candidate = directory_path / name
            if candidate.is_symlink():
                raise SnapshotError("snapshot_source_symlink_rejected")
            resolved = candidate.resolve()
            try:
                relative = resolved.relative_to(root)
            except ValueError as exc:
                raise SnapshotError("snapshot_source_path_escape") from exc
            results.append((relative, resolved))
    return results


def _copy_stable_file(source, target, *, attempts=3):
    target.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(attempts):
        before = _stat_signature(source)
        temporary = target.with_name(
            f".{target.name}.{secrets.token_hex(6)}.partial"
        )
        digest = hashlib.sha256()
        size = 0
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        source_fd = os.open(source, flags)
        target_fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            with os.fdopen(source_fd, "rb", closefd=True) as reader:
                source_fd = -1
                with os.fdopen(target_fd, "wb", closefd=True) as writer:
                    target_fd = -1
                    for chunk in iter(lambda: reader.read(1024 * 1024), b""):
                        writer.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                    writer.flush()
                    os.fsync(writer.fileno())
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        finally:
            if source_fd >= 0:
                os.close(source_fd)
            if target_fd >= 0:
                os.close(target_fd)
        after = _stat_signature(source)
        if before == after and size == before[2]:
            os.replace(temporary, target)
            return {
                "size_bytes": size,
                "sha256": digest.hexdigest(),
                "source_signature": list(after),
            }
        temporary.unlink(missing_ok=True)
    raise SnapshotError("snapshot_source_changed_during_copy")


def _copy_tree(category, source_root, workspace):
    target_root = workspace / category
    target_root.mkdir(parents=True, exist_ok=True)
    records = {}
    for relative, source in _safe_files(source_root):
        record = _copy_stable_file(source, target_root / relative)
        key = f"{category}/{relative.as_posix()}"
        records[key] = {
            **record,
            "category": category,
            "relative_path": relative.as_posix(),
        }
    return records


def _reconcile_tree(category, source_root, workspace, records):
    target_root = workspace / category
    target_root.mkdir(parents=True, exist_ok=True)
    current = {}
    for relative, source in _safe_files(source_root):
        key = f"{category}/{relative.as_posix()}"
        signature = list(_stat_signature(source))
        existing = records.get(key)
        target = target_root / relative
        if (
            existing
            and existing.get("source_signature") == signature
            and target.is_file()
        ):
            current[key] = existing
            continue
        record = _copy_stable_file(source, target)
        current[key] = {
            **record,
            "category": category,
            "relative_path": relative.as_posix(),
        }
    removed = set(records) - set(current)
    for key in sorted(removed):
        relative = Path(records[key]["relative_path"])
        target = (target_root / relative).resolve()
        try:
            target.relative_to(target_root.resolve())
        except ValueError as exc:
            raise SnapshotError("snapshot_target_path_escape") from exc
        if target.is_file():
            target.unlink()
    return current


def _assert_source_trees_stable(roots, records):
    for category, source_root in roots.items():
        observed = {}
        for relative, source in _safe_files(source_root):
            key = f"{category}/{relative.as_posix()}"
            observed[key] = list(_stat_signature(source))
        expected = {
            key: value["source_signature"]
            for key, value in records.items()
            if value["category"] == category
        }
        if observed != expected:
            raise SnapshotError("snapshot_untracked_source_mutation")


def _snapshot_sqlite(source_path, target_path):
    if not source_path.is_file() or source_path.is_symlink():
        raise SnapshotError("snapshot_database_missing")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    target = sqlite3.connect(target_path)
    try:
        source.backup(target)
        integrity = target.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = target.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok" or foreign_keys:
            raise SnapshotError("snapshot_database_integrity_failed")
    finally:
        target.close()
        source.close()
    return {
        "size_bytes": target_path.stat().st_size,
        "sha256": _sha256_file(target_path),
        "integrity": "ok",
        "foreign_key_violations": 0,
    }


def _validate_faiss_coherence(database_path, faiss_root):
    try:
        import faiss
    except ImportError as exc:
        raise SnapshotError("snapshot_faiss_validation_unavailable") from exc
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    try:
        columns = {
            row[1]
            for row in connection.execute('PRAGMA table_info("core_pdffile")')
        }
        lifecycle_clause = (
            "AND lifecycle IN ('uploaded', 'processing', 'ready') "
            if "lifecycle" in columns
            else ""
        )
        rows = connection.execute(
            "SELECT folder_id, page_chunks, chunk_embeddings "
            "FROM core_pdffile "
            "WHERE folder_id IS NOT NULL "
            f"{lifecycle_clause}ORDER BY id"
        ).fetchall()
        unavailable_records = (
            [
                {
                    "id": row[0],
                    "lifecycle": row[1],
                    "storage_key_status": storage_key_status(row[2]),
                }
                for row in connection.execute(
                    "SELECT id, lifecycle, file FROM core_pdffile "
                    "WHERE lifecycle = 'unavailable' ORDER BY id"
                )
            ]
            if "lifecycle" in columns
            else []
        )
    except sqlite3.Error as exc:
        raise SnapshotError("snapshot_faiss_metadata_unavailable") from exc
    finally:
        connection.close()
    expected = {}
    dimensions = {}
    for folder_id, chunks_raw, embeddings_raw in rows:
        try:
            chunks = (
                json.loads(chunks_raw)
                if isinstance(chunks_raw, str)
                else chunks_raw
            )
            embeddings = (
                json.loads(embeddings_raw)
                if isinstance(embeddings_raw, str)
                else embeddings_raw
            )
        except (TypeError, ValueError):
            continue
        if (
            not isinstance(chunks, list)
            or not isinstance(embeddings, list)
            or not chunks
            or len(chunks) != len(embeddings)
            or not all(isinstance(vector, list) and vector for vector in embeddings)
        ):
            continue
        vector_dimensions = {len(vector) for vector in embeddings}
        if len(vector_dimensions) != 1:
            raise SnapshotError("snapshot_embedding_dimensions_inconsistent")
        dimension = next(iter(vector_dimensions))
        if folder_id in dimensions and dimensions[folder_id] != dimension:
            raise SnapshotError("snapshot_embedding_dimensions_inconsistent")
        dimensions[folder_id] = dimension
        expected[folder_id] = expected.get(folder_id, 0) + len(embeddings)

    evidence = {}
    for folder_id, count in sorted(expected.items()):
        path = faiss_root / f"folder_{folder_id}.index"
        if not path.is_file():
            raise SnapshotError("snapshot_faiss_index_missing")
        try:
            index = faiss.read_index(str(path))
        except Exception as exc:
            raise SnapshotError("snapshot_faiss_index_unreadable") from exc
        if int(index.ntotal) != count or int(index.d) != dimensions[folder_id]:
            raise SnapshotError("snapshot_faiss_count_mismatch")
        evidence[str(folder_id)] = {
            "vector_count": count,
            "dimensions": dimensions[folder_id],
            "sha256": _sha256_file(path),
        }
    evidence["unavailable_documents"] = build_unavailable_attestation(
        unavailable_records
    )
    return evidence


def _canonical_digest(records):
    canonical = [
        {
            "path": key,
            "size_bytes": value["size_bytes"],
            "sha256": value["sha256"],
        }
        for key, value in sorted(records.items())
    ]
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest(), canonical


def _make_read_only(root):
    for directory, directories, files in os.walk(root, topdown=False):
        for name in files:
            os.chmod(Path(directory) / name, 0o440)
        for name in directories:
            os.chmod(Path(directory) / name, 0o550)
    os.chmod(root, 0o550)


def _source_roots():
    return {
        "media": Path(settings.MEDIA_ROOT).resolve(),
        "pdf_cache": Path(settings.PDF_CACHE_DIR).resolve(),
        "faiss_indexes": Path(settings.FAISS_INDEX_DIR).resolve(),
        "chroma_db": Path(settings.CHROMA_DIR).resolve(),
        "staticfiles": Path(settings.STATIC_ROOT).resolve(),
    }


def create_consistent_snapshot(
    job,
    *,
    cancellation_check=None,
    progress_callback=None,
    source_roots=None,
    database_path=None,
    snapshot_root=None,
):
    """Create and freeze one coherent local source snapshot."""
    cancellation_check = cancellation_check or (lambda: False)
    progress_callback = progress_callback or (lambda **kwargs: None)
    source_deployment = deployment_id()
    snapshot = SourceSnapshot.objects.create(
        job=job,
        deployment_id=source_deployment,
        initial_epoch=current_epoch(source_deployment=source_deployment),
    )
    root = Path(snapshot_root or settings.VAULT_SNAPSHOT_ROOT).resolve()
    root.mkdir(parents=True, exist_ok=True)
    incomplete = root / f".{snapshot.public_id}.incomplete"
    incomplete.mkdir(mode=0o700)
    snapshot.state = SourceSnapshot.State.COPYING
    snapshot.workspace_path = str(incomplete)
    snapshot.save(update_fields=["state", "workspace_path", "updated_at"])
    step, _ = VaultJobStep.objects.get_or_create(
        job=job,
        phase="snapshot",
        defaults={"status": VaultJobStep.Status.RUNNING},
    )
    step.status = VaultJobStep.Status.RUNNING
    step.started_at = step.started_at or timezone.now()
    step.save(update_fields=["status", "started_at", "updated_at"])
    barrier_acquired = False
    started = time.monotonic()
    try:
        records = {}
        roots = source_roots or _source_roots()
        roots = {
            category: Path(source_root).resolve()
            for category, source_root in roots.items()
        }
        for category, source_root in roots.items():
            if cancellation_check():
                raise SnapshotCancelled("snapshot_cancelled")
            records.update(_copy_tree(category, source_root, incomplete))
            progress_callback(
                phase="snapshotting",
                progress={
                    "category": category,
                    "files_copied": len(records),
                },
            )

        snapshot.state = SourceSnapshot.State.BARRIER
        snapshot.save(update_fields=["state", "updated_at"])
        request_barrier(
            owner_job_id=job.public_id,
            source_deployment=source_deployment,
        )
        barrier_acquired = True
        progress_callback(
            phase="snapshot_barrier",
            progress={"active_mutations": 0},
        )
        assert_barrier_owner(
            owner_job_id=job.public_id,
            source_deployment=source_deployment,
        )
        database_record = _snapshot_sqlite(
            Path(
                database_path or settings.DATABASES["default"]["NAME"]
            ).resolve(),
            incomplete / "db.sqlite3",
        )
        records["db.sqlite3"] = {
            **database_record,
            "category": "database",
            "relative_path": "db.sqlite3",
        }
        for category, source_root in roots.items():
            category_records = {
                key: value
                for key, value in records.items()
                if value["category"] == category
            }
            reconciled = _reconcile_tree(
                category,
                source_root,
                incomplete,
                category_records,
            )
            records = {
                key: value
                for key, value in records.items()
                if value["category"] != category
            }
            records.update(reconciled)

        state = assert_barrier_owner(
            owner_job_id=job.public_id,
            source_deployment=source_deployment,
        )
        included_epoch = state.current_epoch
        changes = journal_since(
            snapshot.initial_epoch,
            source_deployment=source_deployment,
        )
        _assert_source_trees_stable(roots, records)
        snapshot_digest, canonical_files = _canonical_digest(records)
        inventory = build_manifest(
            incomplete,
            database_path=incomplete / "db.sqlite3",
            media_root=incomplete / "media",
            faiss_root=incomplete / "faiss_indexes",
            chroma_root=incomplete / "chroma_db",
            static_root=incomplete / "staticfiles",
        )
        faiss_evidence = _validate_faiss_coherence(
            incomplete / "db.sqlite3",
            incomplete / "faiss_indexes",
        )
        evidence = {
            "snapshot_schema": 1,
            "snapshot_id": str(snapshot.public_id),
            "deployment_id": source_deployment,
            "initial_epoch": snapshot.initial_epoch,
            "included_epoch": included_epoch,
            "journal_changes": changes,
            "index_writers_drained": True,
            "database": database_record,
            "faiss": faiss_evidence,
            "files": canonical_files,
            "inventory": inventory,
            "duration_seconds": round(time.monotonic() - started, 3),
        }
        evidence_path = incomplete / "snapshot-evidence.json"
        evidence_path.write_text(
            json.dumps(evidence, sort_keys=True, indent=2, default=str),
            encoding="utf-8",
        )
        final_path = root / f"{snapshot.public_id}-{snapshot_digest[:12]}"
        _make_read_only(incomplete)
        os.replace(incomplete, final_path)
        snapshot.state = SourceSnapshot.State.FINALIZED
        snapshot.included_epoch = included_epoch
        snapshot.snapshot_digest = snapshot_digest
        snapshot.workspace_path = str(final_path)
        snapshot.file_count = len(canonical_files)
        snapshot.byte_count = sum(item["size_bytes"] for item in canonical_files)
        snapshot.evidence = {
            "snapshot_schema": 1,
            "evidence_path": "snapshot-evidence.json",
            "database_sha256": database_record["sha256"],
            "journal_change_count": len(changes),
            "index_writers_drained": True,
        }
        snapshot.finalized_at = timezone.now()
        snapshot.save(
            update_fields=[
                "state",
                "included_epoch",
                "snapshot_digest",
                "workspace_path",
                "file_count",
                "byte_count",
                "evidence",
                "finalized_at",
                "updated_at",
            ]
        )
        step.status = VaultJobStep.Status.COMPLETED
        step.checkpoint = {
            "snapshot_id": str(snapshot.public_id),
            "snapshot_digest": snapshot_digest,
            "included_epoch": included_epoch,
        }
        step.completed_objects = snapshot.file_count
        step.completed_bytes = snapshot.byte_count
        step.finished_at = timezone.now()
        step.save(
            update_fields=[
                "status",
                "checkpoint",
                "completed_objects",
                "completed_bytes",
                "finished_at",
                "updated_at",
            ]
        )
        return snapshot
    except Exception as exc:
        snapshot.state = SourceSnapshot.State.FAILED
        snapshot.safe_error_code = getattr(
            exc, "reason_code", "consistent_snapshot_unproven"
        )
        snapshot.save(
            update_fields=["state", "safe_error_code", "updated_at"]
        )
        step.status = VaultJobStep.Status.FAILED
        step.checkpoint = {"safe_error_code": snapshot.safe_error_code}
        step.finished_at = timezone.now()
        step.save(
            update_fields=[
                "status",
                "checkpoint",
                "finished_at",
                "updated_at",
            ]
        )
        if isinstance(exc, (SnapshotError, ConsistentSnapshotUnproven)):
            raise
        raise SnapshotError("consistent_snapshot_unproven") from exc
    finally:
        if barrier_acquired:
            release_barrier(
                owner_job_id=job.public_id,
                source_deployment=source_deployment,
                tolerate_lost=True,
            )
