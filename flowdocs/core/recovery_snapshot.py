"""Product-neutral, stable local snapshot capture for recovery operations."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping


class SnapshotCaptureError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False):
        self.code = code
        self.retryable = retryable
        super().__init__(code)


@dataclass(frozen=True)
class ConsistencySnapshot:
    public_id: str
    workspace_path: str
    included_epoch: int
    evidence_sha256: str
    database_sha256: str
    configuration_sha256: str


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _hash_file(path: Path) -> tuple[str, int]:
    hasher = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
            size += len(chunk)
    return hasher.hexdigest(), size


def _safe_root(path: Path, code: str) -> Path:
    if path.is_symlink() or not path.is_dir():
        raise SnapshotCaptureError(code)
    return path.resolve()


def _scan_sources(source_roots: Mapping[str, Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for category, configured in sorted(source_roots.items()):
        root = _safe_root(Path(configured), "snapshot_source_root_unsafe")
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise SnapshotCaptureError("snapshot_source_path_unsafe")
            if not path.is_file():
                continue
            try:
                metadata = path.stat(follow_symlinks=False)
            except OSError as exc:
                raise SnapshotCaptureError(
                    "snapshot_source_mutated", retryable=True
                ) from exc
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise SnapshotCaptureError("snapshot_source_file_unsafe")
            relative = path.relative_to(root).as_posix()
            records.append(
                {
                    "category": category,
                    "relative": relative,
                    "path": f"{category}/{relative}",
                    "size_bytes": metadata.st_size,
                    "mtime_ns": metadata.st_mtime_ns,
                    "inode": metadata.st_ino,
                    "source": str(path),
                }
            )
    return records


def _stable_identity(record: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        record["category"],
        record["relative"],
        record["size_bytes"],
        record["mtime_ns"],
        record["inode"],
    )


def _copy_scanned_files(
    records: list[dict[str, Any]],
    destination: Path,
) -> list[dict[str, Any]]:
    copied: list[dict[str, Any]] = []
    for record in records:
        source = Path(record["source"])
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(source, flags)
        except OSError as exc:
            raise SnapshotCaptureError(
                "snapshot_source_mutated", retryable=True
            ) from exc
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            os.close(descriptor)
            raise SnapshotCaptureError("snapshot_source_file_unsafe")
        if _stable_identity(
            {
                **record,
                "size_bytes": before.st_size,
                "mtime_ns": before.st_mtime_ns,
                "inode": before.st_ino,
            }
        ) != _stable_identity(record):
            os.close(descriptor)
            raise SnapshotCaptureError("snapshot_source_mutated", retryable=True)
        target = destination / record["path"]
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        hasher = hashlib.sha256()
        observed = 0
        with os.fdopen(descriptor, "rb") as input_stream, target.open(
            "xb"
        ) as output_stream:
            for chunk in iter(lambda: input_stream.read(1024 * 1024), b""):
                output_stream.write(chunk)
                hasher.update(chunk)
                observed += len(chunk)
            output_stream.flush()
            os.fsync(output_stream.fileno())
            descriptor_after = os.fstat(input_stream.fileno())
        after = source.stat(follow_symlinks=False)
        if (
            observed != record["size_bytes"]
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ino != after.st_ino
            or before.st_size != descriptor_after.st_size
            or before.st_mtime_ns != descriptor_after.st_mtime_ns
            or before.st_ino != descriptor_after.st_ino
        ):
            raise SnapshotCaptureError("snapshot_source_mutated", retryable=True)
        os.chmod(target, 0o400)
        copied.append(
            {
                "path": record["path"],
                "category": record["category"],
                "size_bytes": observed,
                "sha256": hasher.hexdigest(),
            }
        )
    return copied


def _snapshot_sqlite(source: Path, target: Path) -> tuple[str, int]:
    if source.is_symlink() or not source.is_file():
        raise SnapshotCaptureError("snapshot_database_missing")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    source_uri = f"file:{source.resolve().as_posix()}?mode=ro"
    try:
        with sqlite3.connect(source_uri, uri=True) as source_db:
            with sqlite3.connect(target) as target_db:
                source_db.backup(target_db)
        with sqlite3.connect(f"file:{target.as_posix()}?mode=ro", uri=True) as db:
            integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_keys = db.execute("PRAGMA foreign_key_check").fetchall()
    except sqlite3.Error as exc:
        raise SnapshotCaptureError("snapshot_database_failed", retryable=True) from exc
    if integrity != "ok":
        raise SnapshotCaptureError("snapshot_database_integrity_failed")
    if foreign_keys:
        raise SnapshotCaptureError("snapshot_database_foreign_keys_failed")
    os.chmod(target, 0o400)
    return _hash_file(target)


def _table_count(connection: sqlite3.Connection, table: str) -> int:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if not exists:
        return 0
    return int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def _database_inventory(database: Path) -> dict[str, Any]:
    with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as db:
        latest = ""
        if _table_count(db, "django_migrations"):
            row = db.execute(
                "SELECT app, name FROM django_migrations "
                "ORDER BY applied DESC, app DESC, name DESC LIMIT 1"
            ).fetchone()
            latest = f"{row[0]}.{row[1]}" if row else ""
        return {
            "database": {"migrations": {"latest": latest}},
            "counts": {
                "pdf_rows": _table_count(db, "core_pdffile"),
                "folders": _table_count(db, "core_folder"),
                "users": _table_count(db, "auth_user"),
            },
        }


def _configuration_digest(configuration: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(dict(configuration))).hexdigest()


def verify_consistency_snapshot(
    workspace: str | Path,
    *,
    expected_snapshot_id: str,
) -> ConsistencySnapshot:
    root = Path(workspace)
    if root.is_symlink() or not root.is_dir():
        raise SnapshotCaptureError("snapshot_resume_invalid")
    root = root.resolve()
    evidence_path = root / "snapshot-evidence.json"
    if evidence_path.is_symlink() or not evidence_path.is_file():
        raise SnapshotCaptureError("snapshot_resume_invalid")
    try:
        evidence_bytes = evidence_path.read_bytes()
        evidence = json.loads(evidence_bytes)
    except (OSError, ValueError) as exc:
        raise SnapshotCaptureError("snapshot_resume_invalid") from exc
    if evidence.get("snapshot_id") != expected_snapshot_id:
        raise SnapshotCaptureError("snapshot_resume_invalid")
    records = evidence.get("files")
    if not isinstance(records, list) or not records:
        raise SnapshotCaptureError("snapshot_resume_invalid")
    for record in records:
        if not isinstance(record, Mapping):
            raise SnapshotCaptureError("snapshot_resume_invalid")
        relative = Path(str(record.get("path") or ""))
        if relative.is_absolute() or ".." in relative.parts:
            raise SnapshotCaptureError("snapshot_resume_invalid")
        candidate = root / relative
        if candidate.is_symlink() or not candidate.is_file():
            raise SnapshotCaptureError("snapshot_resume_invalid")
        digest, size = _hash_file(candidate)
        if digest != record.get("sha256") or size != record.get("size_bytes"):
            raise SnapshotCaptureError("snapshot_resume_invalid")
    declared_evidence_sha256 = str(evidence.pop("evidence_sha256", ""))
    evidence_sha256 = hashlib.sha256(_canonical_json(evidence)).hexdigest()
    if evidence_sha256 != declared_evidence_sha256:
        raise SnapshotCaptureError("snapshot_resume_invalid")
    return ConsistencySnapshot(
        public_id=expected_snapshot_id,
        workspace_path=str(root),
        included_epoch=int(evidence["included_epoch"]),
        evidence_sha256=evidence_sha256,
        database_sha256=str(evidence["database_sha256"]),
        configuration_sha256=str(
            evidence["configuration_fingerprint"]["sha256"]
        ),
    )


def capture_consistency_snapshot(
    *,
    snapshot_id: str,
    source_roots: Mapping[str, Path],
    database_path: str | Path,
    workspace_root: str | Path,
    configuration: Mapping[str, Any] | None = None,
    max_attempts: int = 3,
    progress_callback: Callable[[str], Any] | None = None,
) -> ConsistencySnapshot:
    """Capture a convergent two-scan snapshot or fail without trusting it."""

    try:
        normalized_id = str(uuid.UUID(str(snapshot_id)))
    except ValueError as exc:
        raise SnapshotCaptureError("snapshot_id_invalid") from exc
    raw_workspace_root = Path(workspace_root)
    if raw_workspace_root.is_symlink():
        raise SnapshotCaptureError("snapshot_workspace_root_unsafe")
    root = raw_workspace_root.resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    final = root / f"snapshot-{normalized_id}"
    if final.exists():
        return verify_consistency_snapshot(
            final,
            expected_snapshot_id=normalized_id,
        )
    configuration_sha256 = _configuration_digest(configuration or {})
    last_failure: SnapshotCaptureError | None = None
    for attempt in range(1, max(1, max_attempts) + 1):
        temporary = root / f".snapshot-{normalized_id}-attempt-{attempt}"
        if temporary.exists():
            shutil.rmtree(temporary)
        temporary.mkdir(mode=0o700)
        try:
            before = _scan_sources(source_roots)
            if progress_callback:
                progress_callback("scan_before")
            database_sha256, database_size = _snapshot_sqlite(
                Path(database_path),
                temporary / "db.sqlite3",
            )
            files = [
                {
                    "path": "db.sqlite3",
                    "category": "database",
                    "size_bytes": database_size,
                    "sha256": database_sha256,
                }
            ]
            files.extend(_copy_scanned_files(before, temporary))
            if progress_callback:
                progress_callback("copy_complete")
            after = _scan_sources(source_roots)
            if [_stable_identity(item) for item in before] != [
                _stable_identity(item) for item in after
            ]:
                raise SnapshotCaptureError(
                    "snapshot_source_mutated",
                    retryable=True,
                )
            inventory = _database_inventory(temporary / "db.sqlite3")
            evidence = {
                "schema_version": 1,
                "snapshot_id": normalized_id,
                "included_epoch": time.time_ns(),
                "attempt": attempt,
                "source_stable": True,
                "consistency": {
                    "sqlite_integrity": "ok",
                    "foreign_keys": "ok",
                },
                "database_sha256": database_sha256,
                "configuration_fingerprint": {
                    "sha256": configuration_sha256,
                },
                "files": files,
                "inventory": inventory,
                "faiss": {
                    "coherent": any(
                        item["category"] == "faiss_indexes" for item in files
                    ),
                    "unavailable_documents": {"count": 0},
                },
            }
            evidence["evidence_sha256"] = hashlib.sha256(
                _canonical_json(evidence)
            ).hexdigest()
            evidence_bytes = _canonical_json(evidence)
            evidence_path = temporary / "snapshot-evidence.json"
            with evidence_path.open("xb") as stream:
                stream.write(evidence_bytes)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(evidence_path, 0o400)
            os.replace(temporary, final)
            return verify_consistency_snapshot(
                final,
                expected_snapshot_id=normalized_id,
            )
        except SnapshotCaptureError as exc:
            last_failure = exc
            shutil.rmtree(temporary, ignore_errors=True)
            if not exc.retryable:
                raise
        except Exception as exc:
            shutil.rmtree(temporary, ignore_errors=True)
            last_failure = SnapshotCaptureError(
                "snapshot_capture_failed",
                retryable=True,
            )
            if attempt >= max(1, max_attempts):
                raise last_failure from exc
    raise SnapshotCaptureError(
        last_failure.code if last_failure else "snapshot_capture_failed",
        retryable=True,
    )


def cleanup_consistency_snapshot(snapshot: ConsistencySnapshot) -> None:
    path = Path(snapshot.workspace_path)
    if path.is_symlink() or not path.name.startswith("snapshot-"):
        raise SnapshotCaptureError("snapshot_cleanup_path_invalid")
    verify_consistency_snapshot(
        path,
        expected_snapshot_id=str(snapshot.public_id),
    )
    shutil.rmtree(path)
