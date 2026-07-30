import gc
import hashlib
import json
import logging
import os
import re
import secrets
import sqlite3
import stat
import time
from pathlib import Path

import numpy as np
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.management.commands.inventory_artifacts import build_manifest
from core.media_quarantine import (
    build_unavailable_attestation,
    storage_key_evidence,
)
from vaultops.models import (
    SourceMutationState,
    SourceSnapshot,
    VaultJob,
    VaultJobStep,
)
from vaultops.services.mutations import (
    ConsistentSnapshotUnproven,
    assert_barrier_owner,
    current_epoch,
    deployment_id,
    journal_since,
    release_barrier,
    request_barrier,
)

logger = logging.getLogger(__name__)


class SnapshotError(RuntimeError):
    reason_code = "consistent_snapshot_unproven"

    def __init__(self, reason_code=None):
        self.reason_code = reason_code or self.reason_code
        super().__init__(self.reason_code)


class SnapshotCancelled(SnapshotError):
    reason_code = "snapshot_cancelled"


class SnapshotCleanupBoundExceeded(SnapshotError):
    def __init__(self, reason_code, *, consumed_bytes):
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.consumed_bytes = consumed_bytes


def stage_snapshot_cleanup(job, *, reason, now=None):
    """Record cleanup intent without touching the filesystem."""
    now = now or timezone.now()
    grace = int(
        getattr(settings, "VAULT_SNAPSHOT_CLEANUP_GRACE_SECONDS", 120)
    )
    count = 0
    snapshots = (
        SourceSnapshot.objects.select_for_update()
        .filter(job=job)
        .exclude(state=SourceSnapshot.State.FINALIZED)
        .order_by("created_at")
    )
    for snapshot in snapshots:
        if not snapshot.workspace_path:
            continue
        if snapshot.cleanup_state in {
            SourceSnapshot.CleanupState.PENDING,
            SourceSnapshot.CleanupState.RECLAIMING,
        }:
            continue
        hard_kill = snapshot.state in {
            SourceSnapshot.State.COPYING,
            SourceSnapshot.State.BARRIER,
        }
        snapshot.cleanup_state = SourceSnapshot.CleanupState.PENDING
        if not snapshot.cleanup_path:
            snapshot.cleanup_path = snapshot.workspace_path
        snapshot.cleanup_not_before = now + timezone.timedelta(
            seconds=grace if hard_kill else 0
        )
        snapshot.cleanup_error_code = ""
        snapshot.evidence = {
            **(snapshot.evidence if isinstance(snapshot.evidence, dict) else {}),
            "workspace_cleanup": {
                "status": "pending",
                "reason": reason,
            },
        }
        snapshot.save(
            update_fields=[
                "cleanup_state",
                "cleanup_path",
                "cleanup_not_before",
                "cleanup_error_code",
                "evidence",
                "updated_at",
            ]
        )
        count += 1
    return count


def _validated_cleanup_paths(snapshot):
    root = Path(settings.VAULT_SNAPSHOT_ROOT).resolve()
    source = root / f".{snapshot.public_id}.incomplete"
    tombstone = root / f".{snapshot.public_id}.cleanup"
    recorded = Path(snapshot.cleanup_path).absolute()
    if (
        recorded not in {source, tombstone}
        or recorded.parent.resolve() != root
    ):
        raise SnapshotError("snapshot_cleanup_path_invalid")
    return source, tombstone


def _remove_cleanup_tree_bounded(path, *, deadline, maximum_bytes):
    """Remove a tree incrementally without crossing configured work bounds."""
    total = 0
    if path.is_symlink():
        path.unlink()
        return total
    stack = [(path, False)]
    while stack:
        if time.monotonic() > deadline:
            raise SnapshotCleanupBoundExceeded(
                "snapshot_cleanup_time_limit_exceeded",
                consumed_bytes=total,
            )
        item, expanded = stack.pop()
        if item.is_symlink():
            item.unlink()
            continue
        if not item.is_dir():
            value = os.stat(item, follow_symlinks=False)
            if total + value.st_size > maximum_bytes:
                raise SnapshotCleanupBoundExceeded(
                    "snapshot_cleanup_byte_limit_exceeded",
                    consumed_bytes=total,
                )
            item.unlink()
            total += value.st_size
            continue
        if expanded:
            item.rmdir()
            continue
        stack.append((item, True))
        children = []
        with os.scandir(item) as entries:
            for entry in entries:
                if time.monotonic() > deadline:
                    raise SnapshotCleanupBoundExceeded(
                        "snapshot_cleanup_time_limit_exceeded",
                        consumed_bytes=total,
                    )
                children.append(Path(entry.path))
        for child in sorted(children, reverse=True):
            stack.append((child, False))
    return total


def _cleanup_owner_is_quiesced(snapshot, *, now):
    job = snapshot.job
    grace = timezone.timedelta(
        seconds=int(
            getattr(settings, "VAULT_SNAPSHOT_CLEANUP_GRACE_SECONDS", 120)
        )
    )
    if (
        job.status
        in {
            VaultJob.Status.CLAIMED,
            VaultJob.Status.RUNNING,
            VaultJob.Status.WAITING,
            VaultJob.Status.CANCELLING,
        }
        or job.claim_token_hash
        or (
            job.heartbeat_at is not None
            and job.heartbeat_at > now - grace
        )
    ):
        return False
    state = (
        SourceMutationState.objects.select_for_update()
        .filter(
            deployment_id=snapshot.deployment_id,
            barrier_owner_job=job.public_id,
        )
        .first()
    )
    if state is not None:
        if state.active_mutations:
            return False
        state.barrier_state = SourceMutationState.BarrierState.OPEN
        state.barrier_owner_job = None
        state.barrier_requested_at = None
        state.barrier_activated_at = None
        state.save(
            update_fields=[
                "barrier_state",
                "barrier_owner_job",
                "barrier_requested_at",
                "barrier_activated_at",
                "updated_at",
            ]
        )
    return True


def reclaim_snapshot_cleanup_intents(*, now=None):
    """Reclaim only due, recorded workspaces within bounded work limits."""
    now = now or timezone.now()
    maximum_items = int(
        getattr(settings, "VAULT_SNAPSHOT_CLEANUP_MAX_ITEMS", 8)
    )
    maximum_scan_items = max(
        maximum_items,
        int(
            getattr(
                settings, "VAULT_SNAPSHOT_CLEANUP_MAX_SCAN_ITEMS", 64
            )
        ),
    )
    remaining_bytes = int(
        getattr(settings, "VAULT_SNAPSHOT_CLEANUP_MAX_BYTES", 536_870_912)
    )
    deadline = time.monotonic() + int(
        getattr(settings, "VAULT_SNAPSHOT_CLEANUP_MAX_SECONDS", 5)
    )
    retry_delay = timezone.timedelta(
        seconds=int(
            getattr(settings, "VAULT_SNAPSHOT_CLEANUP_GRACE_SECONDS", 120)
        )
    )
    candidate_ids = list(
        SourceSnapshot.objects.filter(
            cleanup_state__in={
                SourceSnapshot.CleanupState.PENDING,
                SourceSnapshot.CleanupState.RECLAIMING,
                SourceSnapshot.CleanupState.FAILED,
            },
            cleanup_not_before__lte=now,
        )
        .order_by("cleanup_not_before", "created_at")
        .values_list("pk", flat=True)[:maximum_scan_items]
    )
    reclaimed = 0
    attempted = 0
    for snapshot_id in candidate_ids:
        if (
            attempted >= maximum_items
            or time.monotonic() > deadline
            or remaining_bytes < 0
        ):
            break
        with transaction.atomic(using="control"):
            snapshot = (
                SourceSnapshot.objects.select_for_update()
                .select_related("job")
                .get(pk=snapshot_id)
            )
            if (
                snapshot.cleanup_state
                not in {
                    SourceSnapshot.CleanupState.PENDING,
                    SourceSnapshot.CleanupState.RECLAIMING,
                    SourceSnapshot.CleanupState.FAILED,
                }
                or snapshot.cleanup_not_before > now
            ):
                continue
            if not _cleanup_owner_is_quiesced(snapshot, now=now):
                snapshot.cleanup_not_before = now + retry_delay
                snapshot.save(
                    update_fields=["cleanup_not_before", "updated_at"]
                )
                continue
            try:
                source, tombstone = _validated_cleanup_paths(snapshot)
            except SnapshotError as exc:
                snapshot.cleanup_state = (
                    SourceSnapshot.CleanupState.QUARANTINED
                )
                snapshot.cleanup_not_before = None
                snapshot.cleanup_error_code = exc.reason_code
                snapshot.evidence = {
                    **(
                        snapshot.evidence
                        if isinstance(snapshot.evidence, dict)
                        else {}
                    ),
                    "workspace_cleanup": {
                        "status": "quarantined",
                        "reason_code": exc.reason_code,
                    },
                }
                snapshot.save(
                    update_fields=[
                        "cleanup_state",
                        "cleanup_not_before",
                        "cleanup_error_code",
                        "evidence",
                        "updated_at",
                    ]
                )
                continue
            snapshot.cleanup_state = SourceSnapshot.CleanupState.RECLAIMING
            snapshot.cleanup_path = str(tombstone)
            snapshot.cleanup_not_before = now + retry_delay
            snapshot.cleanup_attempts += 1
            snapshot.cleanup_error_code = ""
            snapshot.save(
                update_fields=[
                    "cleanup_state",
                    "cleanup_path",
                    "cleanup_not_before",
                    "cleanup_attempts",
                    "cleanup_error_code",
                    "updated_at",
                ]
            )
            attempted += 1
        try:
            if source.is_symlink():
                source.unlink()
            elif source.exists():
                if tombstone.exists():
                    raise SnapshotError("snapshot_cleanup_collision")
                os.replace(source, tombstone)
            if tombstone.is_symlink():
                tombstone.unlink()
                consumed = 0
            elif tombstone.exists():
                consumed = _remove_cleanup_tree_bounded(
                    tombstone,
                    deadline=deadline,
                    maximum_bytes=remaining_bytes,
                )
            else:
                consumed = 0
        except Exception as exc:
            remaining_bytes -= int(getattr(exc, "consumed_bytes", 0))
            reason_code = getattr(
                exc, "reason_code", "snapshot_workspace_cleanup_failed"
            )
            with transaction.atomic(using="control"):
                snapshot = SourceSnapshot.objects.select_for_update().get(
                    pk=snapshot_id
                )
                snapshot.cleanup_state = SourceSnapshot.CleanupState.FAILED
                snapshot.cleanup_not_before = now + retry_delay
                snapshot.cleanup_error_code = reason_code
                snapshot.evidence = {
                    **(
                        snapshot.evidence
                        if isinstance(snapshot.evidence, dict)
                        else {}
                    ),
                    "workspace_cleanup": {
                        "status": "failed",
                        "reason_code": reason_code,
                    },
                }
                snapshot.save(
                    update_fields=[
                        "cleanup_state",
                        "cleanup_not_before",
                        "cleanup_error_code",
                        "evidence",
                        "updated_at",
                    ]
                )
            continue
        remaining_bytes -= consumed
        with transaction.atomic(using="control"):
            snapshot = SourceSnapshot.objects.select_for_update().get(
                pk=snapshot_id
            )
            snapshot.cleanup_state = SourceSnapshot.CleanupState.COMPLETED
            snapshot.cleanup_error_code = ""
            snapshot.workspace_path = ""
            snapshot.evidence = {
                **(
                    snapshot.evidence
                    if isinstance(snapshot.evidence, dict)
                    else {}
                ),
                "workspace_cleanup": {
                    "status": "completed",
                    "bytes": consumed,
                },
            }
            snapshot.save(
                update_fields=[
                    "cleanup_state",
                    "cleanup_error_code",
                    "workspace_path",
                    "evidence",
                    "updated_at",
                ]
            )
        reclaimed += 1
    return reclaimed


def reclaim_snapshot_cleanup_intents_safely():
    try:
        reclaim_snapshot_cleanup_intents()
    except Exception:
        # Retry acceptance is already durable. Cleanup retains its own state
        # and must never turn a committed 202 response into a 503.
        return 0
    return 1


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
        searchable_clause = _searchable_sql_contract(connection)
        rows = connection.execute(
            "SELECT folder_id, page_chunks, chunk_embeddings "
            "FROM core_pdffile "
            "WHERE folder_id IS NOT NULL "
            f"{searchable_clause}ORDER BY id"
        ).fetchall()
        unavailable_attestation = (
            build_unavailable_attestation(
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
                for row in connection.execute(
                    "SELECT id, lifecycle, file, media_expected_sha256, "
                    "media_expected_size, media_prior_lifecycle "
                    "FROM core_pdffile "
                    "WHERE lifecycle = 'unavailable' ORDER BY id"
                )
                )
            )
            if "lifecycle" in columns
            else build_unavailable_attestation(())
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
    evidence["unavailable_documents"] = unavailable_attestation
    return evidence


def _searchable_sql_contract(connection):
    columns = {
        row[1]
        for row in connection.execute('PRAGMA table_info("core_pdffile")')
    }
    clauses = []
    if "lifecycle" in columns:
        clauses.append(
            "lifecycle IN ('uploaded', 'processing', 'ready')"
        )
    if "indexed" in columns:
        clauses.append("indexed = 1")
    return "".join(f"AND {clause} " for clause in clauses)


def _preflight_searchable_embeddings(connection, lifecycle_clause):
    row_count, source_bytes, max_cell_bytes = connection.execute(
        "SELECT COUNT(*), "
        "COALESCE(SUM(COALESCE(LENGTH(page_chunks), 0) + "
        "COALESCE(LENGTH(chunk_embeddings), 0)), 0), "
        "COALESCE(MAX(MAX(COALESCE(LENGTH(page_chunks), 0), "
        "COALESCE(LENGTH(chunk_embeddings), 0))), 0) "
        "FROM core_pdffile WHERE folder_id IS NOT NULL "
        f"{lifecycle_clause}"
    ).fetchone()
    if row_count > settings.VAULT_SNAPSHOT_FAISS_MAX_PDFS:
        raise SnapshotError("snapshot_faiss_rebuild_pdf_limit_exceeded")
    if source_bytes > settings.VAULT_SNAPSHOT_FAISS_MAX_SOURCE_BYTES:
        raise SnapshotError("snapshot_faiss_rebuild_source_limit_exceeded")
    if max_cell_bytes > settings.VAULT_SNAPSHOT_FAISS_MAX_PDF_JSON_BYTES:
        raise SnapshotError("snapshot_faiss_rebuild_cell_limit_exceeded")
    folder_ids = [
        int(row[0])
        for row in connection.execute(
            "SELECT DISTINCT folder_id FROM core_pdffile "
            "WHERE folder_id IS NOT NULL "
            f"{lifecycle_clause}ORDER BY folder_id"
        )
    ]
    return folder_ids, {
        "pdf_count": int(row_count),
        "source_json_bytes": int(source_bytes),
    }


def _normalized_pdf_batch(
    chunks_raw,
    embeddings_raw,
    *,
    cancellation_check,
    remaining_vectors=None,
    remaining_bytes=None,
):
    try:
        chunks = json.loads(chunks_raw)
        embeddings = json.loads(embeddings_raw)
    except (TypeError, ValueError) as exc:
        raise SnapshotError("snapshot_searchable_embeddings_invalid") from exc
    if (
        not isinstance(chunks, list)
        or not isinstance(embeddings, list)
        or not chunks
        or len(chunks) != len(embeddings)
        or len(chunks) > settings.VAULT_SNAPSHOT_FAISS_MAX_CHUNKS_PER_PDF
    ):
        raise SnapshotError("snapshot_searchable_embeddings_invalid")
    if remaining_vectors is not None and len(embeddings) > remaining_vectors:
        raise SnapshotError("snapshot_faiss_rebuild_vector_limit_exceeded")
    normalized = []
    dimension = None
    for chunk, embedding in zip(chunks, embeddings):
        if cancellation_check():
            raise SnapshotCancelled("snapshot_cancelled")
        if (
            not isinstance(chunk, str)
            or not chunk.strip()
            or not isinstance(embedding, list)
            or not embedding
        ):
            raise SnapshotError("snapshot_searchable_embeddings_invalid")
        try:
            vector = np.asarray(embedding, dtype=np.float64)
        except (TypeError, ValueError, OverflowError) as exc:
            raise SnapshotError("snapshot_searchable_embeddings_invalid") from exc
        if (
            vector.ndim != 1
            or vector.size > settings.VAULT_SNAPSHOT_FAISS_MAX_DIMENSIONS
            or not np.isfinite(vector).all()
        ):
            raise SnapshotError("snapshot_searchable_embeddings_invalid")
        maximum = float(np.max(np.abs(vector)))
        if not np.isfinite(maximum) or maximum <= 0:
            raise SnapshotError("snapshot_searchable_embeddings_invalid")
        scaled = vector / maximum
        norm = float(np.linalg.norm(scaled))
        if not np.isfinite(norm) or norm <= 0:
            raise SnapshotError("snapshot_searchable_embeddings_invalid")
        candidate = (scaled / norm).astype(np.float32)
        if (
            not np.isfinite(candidate).all()
            or not np.any(candidate)
        ):
            raise SnapshotError("snapshot_searchable_embeddings_invalid")
        if dimension is not None and dimension != int(candidate.shape[0]):
            raise SnapshotError("snapshot_embedding_dimensions_inconsistent")
        dimension = int(candidate.shape[0])
        projected_bytes = (len(normalized) + 1) * dimension * 4
        if (
            remaining_bytes is not None
            and projected_bytes > remaining_bytes
        ):
            raise SnapshotError("snapshot_faiss_rebuild_byte_limit_exceeded")
        normalized.append(candidate)
    if cancellation_check():
        raise SnapshotCancelled("snapshot_cancelled")
    return np.vstack(normalized).astype(np.float32, copy=False)


def _reconcile_candidate_faiss(
    database_path,
    faiss_root,
    *,
    cancellation_check,
    source_faiss_records=None,
):
    """Keep coherent indexes and derive stale ones only in the snapshot."""
    try:
        import faiss
    except ImportError as exc:
        raise SnapshotError("snapshot_faiss_validation_unavailable") from exc

    faiss_root.mkdir(parents=True, exist_ok=True)
    folders = {}
    source_folders = []
    for path, record in (source_faiss_records or {}).items():
        matched = re.fullmatch(r"faiss_indexes/folder_([0-9]+)\.index", path)
        if matched and str(int(matched.group(1))) == matched.group(1):
            source_folders.append(
                {
                    "folder_id": matched.group(1),
                    "sha256": record["sha256"],
                }
            )
    source_folders.sort(key=lambda item: int(item["folder_id"]))
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    try:
        lifecycle_clause = _searchable_sql_contract(connection)
        folder_ids, totals = _preflight_searchable_embeddings(
            connection, lifecycle_clause
        )
        expected_names = {
            f"folder_{folder_id}.index" for folder_id in folder_ids
        }
        for candidate in sorted(faiss_root.glob("folder_*.index")):
            matched = re.fullmatch(r"folder_([0-9]+)\.index", candidate.name)
            if matched and candidate.name not in expected_names:
                candidate.unlink()
                folders[str(int(matched.group(1)))] = {
                    "disposition": "removed",
                    "pdf_count": 0,
                    "vector_count": 0,
                }

        vector_total = 0
        vector_bytes = 0
        for folder_id in folder_ids:
            if cancellation_check():
                raise SnapshotCancelled("snapshot_cancelled")
            path = faiss_root / f"folder_{folder_id}.index"
            previous = {}
            existing = None
            content_matches = True
            if path.is_file() and not path.is_symlink():
                if path.stat().st_size > settings.VAULT_SNAPSHOT_FAISS_MAX_BYTES:
                    raise SnapshotError(
                        "snapshot_faiss_rebuild_byte_limit_exceeded"
                    )
                previous["sha256"] = _sha256_file(path)
                try:
                    existing = faiss.read_index(str(path))
                except Exception:
                    previous["readable"] = False
                else:
                    previous.update(
                        {
                            "readable": True,
                            "vector_count": int(existing.ntotal),
                            "dimensions": int(existing.d),
                            "index_type": type(existing).__name__,
                        }
                    )
                    if (
                        type(existing).__name__ != "IndexFlatIP"
                        or int(existing.metric_type)
                        != int(faiss.METRIC_INNER_PRODUCT)
                    ):
                        content_matches = False
            else:
                content_matches = False

            pdf_count = 0
            folder_count = 0
            dimension = None
            rows = connection.execute(
                "SELECT id, page_chunks, chunk_embeddings "
                "FROM core_pdffile WHERE folder_id = ? "
                f"{lifecycle_clause}ORDER BY id",
                (folder_id,),
            )
            for _pdf_id, chunks_raw, embeddings_raw in rows:
                if cancellation_check():
                    raise SnapshotCancelled("snapshot_cancelled")
                batch = _normalized_pdf_batch(
                    chunks_raw,
                    embeddings_raw,
                    cancellation_check=cancellation_check,
                    remaining_vectors=(
                        settings.VAULT_SNAPSHOT_FAISS_MAX_VECTORS
                        - vector_total
                    ),
                    remaining_bytes=(
                        settings.VAULT_SNAPSHOT_FAISS_MAX_BYTES
                        - vector_bytes
                    ),
                )
                pdf_count += 1
                projected_count = folder_count + int(batch.shape[0])
                projected_total = vector_total + int(batch.shape[0])
                projected_bytes = vector_bytes + int(batch.nbytes)
                if (
                    projected_count
                    > settings.VAULT_SNAPSHOT_FAISS_MAX_VECTORS
                    or projected_total
                    > settings.VAULT_SNAPSHOT_FAISS_MAX_VECTORS
                ):
                    raise SnapshotError(
                        "snapshot_faiss_rebuild_vector_limit_exceeded"
                    )
                if projected_bytes > settings.VAULT_SNAPSHOT_FAISS_MAX_BYTES:
                    raise SnapshotError(
                        "snapshot_faiss_rebuild_byte_limit_exceeded"
                    )
                if dimension is not None and dimension != int(batch.shape[1]):
                    raise SnapshotError(
                        "snapshot_embedding_dimensions_inconsistent"
                    )
                dimension = int(batch.shape[1])
                if existing is not None and content_matches:
                    try:
                        observed = existing.reconstruct_n(
                            folder_count, int(batch.shape[0])
                        )
                    except Exception:
                        content_matches = False
                    else:
                        observed = np.asarray(observed, dtype=np.float32)
                        content_matches = bool(
                            observed.shape == batch.shape
                            and observed.tobytes(order="C")
                            == batch.tobytes(order="C")
                        )
                folder_count = projected_count
                vector_total = projected_total
                vector_bytes = projected_bytes
            coherent = bool(
                existing is not None
                and content_matches
                and int(existing.ntotal) == folder_count
                and int(existing.d) == dimension
            )
            if coherent:
                folders[str(folder_id)] = {
                    "disposition": "copied",
                    "pdf_count": pdf_count,
                    "vector_count": folder_count,
                    "dimensions": dimension,
                    "sha256": previous["sha256"],
                }
                continue
            if not folder_count or dimension is None:
                raise SnapshotError("snapshot_searchable_embeddings_invalid")
            # Do not retain the copied index while building its replacement.
            existing = None
            rebuilt = faiss.IndexFlatIP(dimension)
            rebuilt_count = 0
            rows = connection.execute(
                "SELECT id, page_chunks, chunk_embeddings "
                "FROM core_pdffile WHERE folder_id = ? "
                f"{lifecycle_clause}ORDER BY id",
                (folder_id,),
            )
            for _pdf_id, chunks_raw, embeddings_raw in rows:
                batch = _normalized_pdf_batch(
                    chunks_raw,
                    embeddings_raw,
                    cancellation_check=cancellation_check,
                    remaining_vectors=folder_count - rebuilt_count,
                    remaining_bytes=(
                        settings.VAULT_SNAPSHOT_FAISS_MAX_BYTES
                        - rebuilt_count * dimension * 4
                    ),
                )
                projected_rebuilt_count = rebuilt_count + int(batch.shape[0])
                if (
                    projected_rebuilt_count > folder_count
                    or projected_rebuilt_count
                    > settings.VAULT_SNAPSHOT_FAISS_MAX_VECTORS
                    or projected_rebuilt_count * dimension * 4
                    > settings.VAULT_SNAPSHOT_FAISS_MAX_BYTES
                ):
                    raise SnapshotError(
                        "snapshot_faiss_rebuild_vector_limit_exceeded"
                    )
                if cancellation_check():
                    raise SnapshotCancelled("snapshot_cancelled")
                rebuilt.add(batch)
                rebuilt_count = projected_rebuilt_count
            if rebuilt_count != folder_count:
                raise SnapshotError("snapshot_faiss_rebuild_verification_failed")
            temporary = path.with_name(
                f".{path.name}.{secrets.token_hex(6)}.partial"
            )
            try:
                if cancellation_check():
                    raise SnapshotCancelled("snapshot_cancelled")
                faiss.write_index(rebuilt, str(temporary))
                with temporary.open("rb") as stream:
                    os.fsync(stream.fileno())
                if cancellation_check():
                    raise SnapshotCancelled("snapshot_cancelled")
                rebuilt = None
                gc.collect()
                written = faiss.read_index(str(temporary))
                if (
                    type(written).__name__ != "IndexFlatIP"
                    or int(written.metric_type)
                    != int(faiss.METRIC_INNER_PRODUCT)
                    or int(written.ntotal) != folder_count
                    or int(written.d) != dimension
                ):
                    raise SnapshotError(
                        "snapshot_faiss_rebuild_verification_failed"
                    )
                os.replace(temporary, path)
            except (SnapshotError, SnapshotCancelled):
                raise
            except Exception as exc:
                raise SnapshotError("snapshot_faiss_rebuild_failed") from exc
            finally:
                temporary.unlink(missing_ok=True)
            folders[str(folder_id)] = {
                "disposition": "rebuilt",
                "pdf_count": pdf_count,
                "vector_count": folder_count,
                "dimensions": dimension,
                "sha256": _sha256_file(path),
                "previous": previous,
            }
        totals.update(
            {
                "vector_count": vector_total,
                "vector_bytes": vector_bytes,
            }
        )
    except sqlite3.Error as exc:
        raise SnapshotError("snapshot_faiss_metadata_unavailable") from exc
    finally:
        connection.close()
    source_digest = hashlib.sha256(
        json.dumps(
            source_folders,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return {
        "schema": 1,
        "source": "stored_embeddings",
        "source_folders": source_folders,
        "source_folders_digest": source_digest,
        **totals,
        "folders": folders,
    }


def _workspace_tree_records(category, root):
    records = {}
    for relative, path in _safe_files(root):
        key = f"{category}/{relative.as_posix()}"
        records[key] = {
            "size_bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
            "category": category,
            "relative_path": relative.as_posix(),
        }
    return records


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


def _record_cleanup_staging_failure(snapshot):
    """Retain a DB-owned recovery intent when normal staging fails."""
    try:
        now = timezone.now()
        retry_delay = timezone.timedelta(
            seconds=int(
                getattr(
                    settings, "VAULT_SNAPSHOT_CLEANUP_GRACE_SECONDS", 120
                )
            )
        )
        with transaction.atomic(using="control"):
            locked = SourceSnapshot.objects.select_for_update().get(
                pk=snapshot.pk
            )
            locked.cleanup_state = SourceSnapshot.CleanupState.FAILED
            locked.cleanup_path = locked.workspace_path
            locked.cleanup_not_before = now + retry_delay
            locked.cleanup_error_code = "snapshot_cleanup_staging_failed"
            locked.evidence = {
                **(
                    locked.evidence
                    if isinstance(locked.evidence, dict)
                    else {}
                ),
                "workspace_cleanup": {
                    "status": "failed",
                    "reason_code": "snapshot_cleanup_staging_failed",
                },
            }
            locked.save(
                update_fields=[
                    "cleanup_state",
                    "cleanup_path",
                    "cleanup_not_before",
                    "cleanup_error_code",
                    "evidence",
                    "updated_at",
                ]
            )
    except Exception:
        logger.exception(
            "snapshot cleanup staging evidence could not be persisted",
            extra={"snapshot_id": str(snapshot.public_id)},
        )


def _record_barrier_release_failure(snapshot):
    try:
        with transaction.atomic(using="control"):
            locked = SourceSnapshot.objects.select_for_update().get(
                pk=snapshot.pk
            )
            locked.evidence = {
                **(
                    locked.evidence
                    if isinstance(locked.evidence, dict)
                    else {}
                ),
                "barrier_release": {
                    "status": "failed",
                    "reason_code": "snapshot_barrier_release_failed",
                },
            }
            locked.save(update_fields=["evidence", "updated_at"])
    except Exception:
        logger.exception(
            "snapshot barrier release evidence could not be persisted",
            extra={"snapshot_id": str(snapshot.public_id)},
        )


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
    primary_exception = None
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
        source_records = dict(records)
        _assert_source_trees_stable(roots, source_records)
        faiss_reconciliation = _reconcile_candidate_faiss(
            incomplete / "db.sqlite3",
            incomplete / "faiss_indexes",
            cancellation_check=cancellation_check,
            source_faiss_records={
                key: value
                for key, value in source_records.items()
                if value["category"] == "faiss_indexes"
            },
        )
        _assert_source_trees_stable(roots, source_records)
        records = {
            key: value
            for key, value in records.items()
            if value["category"] != "faiss_indexes"
        }
        records.update(
            _workspace_tree_records(
                "faiss_indexes",
                incomplete / "faiss_indexes",
            )
        )
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
            "faiss_reconciliation": faiss_reconciliation,
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
            "faiss_reconciliation": faiss_reconciliation,
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
        primary_exception = exc
        primary_reason = getattr(
            exc, "reason_code", "consistent_snapshot_unproven"
        )
        snapshot.state = SourceSnapshot.State.FAILED
        snapshot.safe_error_code = primary_reason
        snapshot.save(
            update_fields=[
                "state",
                "safe_error_code",
                "updated_at",
            ]
        )
        step.status = VaultJobStep.Status.FAILED
        step.checkpoint = {"safe_error_code": primary_reason}
        step.finished_at = timezone.now()
        step.save(
            update_fields=[
                "status",
                "checkpoint",
                "finished_at",
                "updated_at",
            ]
        )
        try:
            with transaction.atomic(using="control"):
                locked_snapshot = (
                    SourceSnapshot.objects.select_for_update().get(
                        pk=snapshot.pk
                    )
                )
                stage_snapshot_cleanup(
                    locked_snapshot.job,
                    reason="snapshot_failed",
                )
                transaction.on_commit(
                    reclaim_snapshot_cleanup_intents_safely,
                    using="control",
                    robust=True,
                )
        except Exception:
            # The primary snapshot failure is already durable. Cleanup
            # bookkeeping is independent and must never replace it.
            _record_cleanup_staging_failure(snapshot)
        if isinstance(exc, (SnapshotError, ConsistentSnapshotUnproven)):
            raise
        raise SnapshotError("consistent_snapshot_unproven") from exc
    finally:
        if barrier_acquired:
            try:
                release_barrier(
                    owner_job_id=job.public_id,
                    source_deployment=source_deployment,
                    tolerate_lost=True,
                )
            except Exception:
                if primary_exception is None:
                    raise
                _record_barrier_release_failure(snapshot)
