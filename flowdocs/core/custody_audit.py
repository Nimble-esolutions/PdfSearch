"""Redacted, read-only custody discovery for missing PDF references."""

from __future__ import annotations

import errno
import hashlib
import hmac
import gzip
import os
import sqlite3
import stat
import time
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import quote


AUDIT_SCHEMA = "pdfsearch-missing-pdf-custody/v1"
EVIDENCE_CLASSES = {
    "manifest_object_metadata_exact",
    "manifest_reference_only",
    "exact_manifest_archive",
    "path_only_candidate",
    "no_match",
}
MAX_ARCHIVES = 32
MAX_ARCHIVE_MEMBERS = 250_000
MAX_CANDIDATE_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_LOGICAL_BYTES = 20 * 1024 * 1024 * 1024
MAX_ARCHIVE_PHYSICAL_BYTES = 20 * 1024 * 1024 * 1024
MAX_ARCHIVE_READ_BYTES = 24 * 1024 * 1024 * 1024
MAX_TOTAL_ARCHIVE_PHYSICAL_BYTES = 40 * 1024 * 1024 * 1024
MAX_TOTAL_ARCHIVE_READ_BYTES = 48 * 1024 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100
MAX_AUDIT_SECONDS = 900
MAX_MISSING_REFERENCES = 10_000
MAX_GENERATIONS = 10_000
MAX_EVIDENCE_PER_REFERENCE = 100
MAX_TOTAL_EVIDENCE = 100_000
READ_CHUNK_BYTES = 1024 * 1024
TAR_BLOCK_BYTES = 512


class CustodyAuditError(RuntimeError):
    """Stable, secret-free custody-audit failure."""


def _stat_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _path_parts(path: Path) -> tuple[str, ...]:
    value = os.fspath(path)
    if not value:
        raise CustodyAuditError("path_unavailable")
    absolute = os.path.abspath(value)
    parts = PurePosixPath(absolute).parts
    if not parts or parts[0] != "/" or any(part in {"", ".", ".."} for part in parts[1:]):
        raise CustodyAuditError("path_unsafe")
    return tuple(parts[1:])


def _open_parent_no_follow(path: Path) -> tuple[int, str]:
    parts = _path_parts(path)
    if not parts:
        raise CustodyAuditError("path_unsafe")
    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    directory_fd = os.open("/", directory_flags)
    try:
        for component in parts[:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
    except OSError as exc:
        os.close(directory_fd)
        raise CustodyAuditError("path_unavailable") from exc
    return directory_fd, parts[-1]


def _open_path_no_follow(
    path: Path,
    *,
    final_flags: int = os.O_RDONLY,
    final_regular: bool = True,
) -> tuple[int, os.stat_result]:
    """Open every absolute-path component through stable directory descriptors."""
    directory_fd, name = _open_parent_no_follow(path)
    if hasattr(os, "O_NOFOLLOW"):
        final_flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(name, final_flags, dir_fd=directory_fd)
    except OSError as exc:
        reason = "path_missing" if exc.errno == errno.ENOENT else "path_unsafe"
        raise CustodyAuditError(reason) from exc
    finally:
        os.close(directory_fd)
    try:
        metadata = os.fstat(descriptor)
    except OSError as exc:
        os.close(descriptor)
        raise CustodyAuditError("path_unavailable") from exc
    if final_regular and not stat.S_ISREG(metadata.st_mode):
        os.close(descriptor)
        raise CustodyAuditError("path_unsafe")
    return descriptor, metadata


def _open_relative_no_follow(root_fd: int, value: str) -> tuple[int, os.stat_result]:
    parts = PurePosixPath(value).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise CustodyAuditError("path_unsafe")
    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    current_fd = os.dup(root_fd)
    try:
        for component in parts[:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        final_flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            final_flags |= os.O_NOFOLLOW
        descriptor = os.open(parts[-1], final_flags, dir_fd=current_fd)
    except OSError as exc:
        reason = "path_missing" if exc.errno == errno.ENOENT else "path_unsafe"
        raise CustodyAuditError(reason) from exc
    finally:
        os.close(current_fd)
    try:
        metadata = os.fstat(descriptor)
    except OSError as exc:
        os.close(descriptor)
        raise CustodyAuditError("path_unavailable") from exc
    if not stat.S_ISREG(metadata.st_mode):
        os.close(descriptor)
        raise CustodyAuditError("path_unsafe")
    return descriptor, metadata


def _token(key: bytes, kind: str, value: str) -> str:
    digest = hmac.new(
        key,
        f"{kind}\0{value}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"hmac-sha256:{digest}"


def read_hmac_key_file(path: Path) -> bytes:
    try:
        descriptor, before = _open_path_no_follow(path)
    except CustodyAuditError as exc:
        raise CustodyAuditError("hmac_key_file_unavailable") from exc
    try:
        if (
            before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or before.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
            or before.st_size < 32
            or before.st_size > 1024
        ):
            raise CustodyAuditError("hmac_key_file_unsafe")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            key = stream.read(1025)
        after = os.fstat(descriptor)
        if _stat_identity(before) != _stat_identity(after):
            raise CustodyAuditError("hmac_key_file_changed")
    except OSError as exc:
        raise CustodyAuditError("hmac_key_file_unavailable") from exc
    finally:
        os.close(descriptor)
    if len(key) < 32:
        raise CustodyAuditError("hmac_key_too_short")
    return key


def _sqlite_uri(parent_fd: int, name: str, fallback_path: Path) -> str:
    descriptor_directory = f"/proc/self/fd/{parent_fd}"
    database_path = (
        f"{descriptor_directory}/{name}"
        if os.path.isdir(descriptor_directory)
        else os.path.abspath(fallback_path)
    )
    return f"file:{quote(database_path, safe='/')}?mode=ro"


def _safe_relative_path(value: Any) -> str | None:
    if not isinstance(value, str) or not value or "\\" in value:
        return None
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        return None
    return parsed.as_posix()


def _stored_path(row: sqlite3.Row, columns: set[str]) -> str | None:
    stored = row["file"] if "file" in columns else None
    declared = row["file_path"] if "file_path" in columns else None
    return _safe_relative_path(stored or declared)


def _relative_regular_exists(root_fd: int, stored_path: str) -> bool:
    try:
        descriptor, _metadata = _open_relative_no_follow(root_fd, stored_path)
    except CustodyAuditError as exc:
        if str(exc) == "path_missing":
            return False
        raise
    os.close(descriptor)
    return True


def missing_pdf_references(database: Path, media_root: Path, key: bytes) -> list[dict[str, Any]]:
    """Return only IDs, HMAC path tokens, and internal paths for missing rows."""
    database_parent_fd = None
    database_fd = None
    try:
        database_parent_fd, database_name = _open_parent_no_follow(database)
        database_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        database_fd = os.open(
            database_name,
            database_flags,
            dir_fd=database_parent_fd,
        )
        database_before = os.fstat(database_fd)
        if not stat.S_ISREG(database_before.st_mode):
            raise CustodyAuditError("path_unsafe")
        media_fd, _media_metadata = _open_path_no_follow(
            media_root,
            final_flags=os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            final_regular=False,
        )
    except (CustodyAuditError, OSError) as exc:
        if database_fd is not None:
            os.close(database_fd)
        if database_parent_fd is not None:
            os.close(database_parent_fd)
        raise CustodyAuditError("application_custody_path_unavailable") from exc
    try:
        connection = sqlite3.connect(
            _sqlite_uri(database_parent_fd, database_name, database),
            uri=True,
        )
        connection.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        os.close(database_fd)
        os.close(database_parent_fd)
        os.close(media_fd)
        raise CustodyAuditError("application_database_unavailable") from exc
    try:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='core_pdffile'"
        ).fetchone()
        if not table:
            raise CustodyAuditError("document_table_unavailable")
        columns = {
            row[1]
            for row in connection.execute('PRAGMA table_info("core_pdffile")')
        }
        selected = ["id"]
        selected.extend(name for name in ("file", "file_path") if name in columns)
        if len(selected) == 1:
            raise CustodyAuditError("document_path_columns_unavailable")
        query = f'SELECT {", ".join(selected)} FROM "core_pdffile" ORDER BY id'
        missing = []
        case_paths: dict[str, str] = {}
        for row in connection.execute(query):
            stored_path = _stored_path(row, columns)
            if not stored_path:
                continue
            case_key = stored_path.casefold()
            previous = case_paths.setdefault(case_key, stored_path)
            if previous != stored_path:
                raise CustodyAuditError("document_path_case_collision")
            if _relative_regular_exists(media_fd, stored_path):
                continue
            missing.append(
                {
                    "row_id": int(row["id"]),
                    "path_token": _token(key, "path", stored_path),
                    "_path": stored_path,
                }
            )
            if len(missing) > MAX_MISSING_REFERENCES:
                raise CustodyAuditError("missing_reference_limit_exceeded")
        try:
            database_after = os.fstat(database_fd)
            path_after = os.stat(
                database_name,
                dir_fd=database_parent_fd,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise CustodyAuditError("application_database_changed") from exc
        if (
            _stat_identity(database_before) != _stat_identity(database_after)
            or _stat_identity(database_before) != _stat_identity(path_after)
        ):
            raise CustodyAuditError("application_database_changed")
        return missing
    except sqlite3.Error as exc:
        raise CustodyAuditError("application_database_read_failed") from exc
    finally:
        connection.close()
        os.close(database_fd)
        os.close(database_parent_fd)
        os.close(media_fd)


def _manifest_path(value: Any) -> str | None:
    path = _safe_relative_path(value)
    if path and path.startswith("media/"):
        return path[len("media/") :]
    return path


def _manifest_matches(
    manifest: dict[str, Any],
    missing_by_path: dict[str, dict[str, Any]],
) -> Iterable[tuple[dict[str, Any], dict[str, Any]]]:
    files = manifest.get("files")
    if not isinstance(files, list):
        return
    seen_case_paths: dict[str, str] = {}
    for entry in files:
        if not isinstance(entry, dict):
            continue
        path = _manifest_path(entry.get("path"))
        if path is None:
            continue
        case_key = path.casefold()
        previous = seen_case_paths.setdefault(case_key, path)
        if previous != path:
            raise CustodyAuditError("manifest_path_case_collision")
        reference = missing_by_path.get(path)
        if reference is not None:
            yield reference, entry


def _valid_manifest_evidence(entry: dict[str, Any]) -> tuple[str, int, str] | None:
    digest = entry.get("sha256")
    size = entry.get("bytes")
    object_key = entry.get("object_key")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
        or isinstance(size, bool)
        or not isinstance(size, int)
        or size < 0
        or not isinstance(object_key, str)
        or not object_key
    ):
        return None
    return digest, size, object_key


def _scan_vault(
    *,
    vault: Any,
    profile: Any,
    references: list[dict[str, Any]],
    key: bytes,
    list_generation_ids: Any,
    verify_generation: Any,
    max_pages: int,
    max_generations: int,
    max_evidence_per_reference: int,
    max_total_evidence: int,
    deadline: float,
) -> tuple[
    dict[int, list[dict[str, Any]]],
    dict[str, set[tuple[str, int]]],
    str,
    dict[str, int],
    dict[str, bool],
]:
    evidence: dict[int, list[dict[str, Any]]] = defaultdict(list)
    expected: dict[str, set[tuple[str, int]]] = defaultdict(set)
    missing_by_path = {item["_path"]: item for item in references}
    try:
        generation_ids, listing_complete = list_generation_ids(
            vault,
            profile,
            max_pages=max_pages,
            max_items=max_generations,
            include_status=True,
        )
    except Exception:
        return (
            evidence,
            expected,
            "unavailable",
            {
                "returned": 0,
                "verified": 0,
                "scanned": 0,
                "listing_complete": False,
            },
            {
                "generation_limit": False,
                "evidence_limit": False,
                "time_limit": False,
            },
        )
    generation_limit = not listing_complete
    selected_generation_ids = generation_ids
    verified_count = 0
    scanned_count = 0
    total_evidence = 0
    evidence_limit = False
    for generation_id in selected_generation_ids:
        if time.monotonic() > deadline:
            return (
                evidence,
                expected,
                "partial",
                {
                    "returned": len(generation_ids),
                    "scanned": scanned_count,
                    "verified": verified_count,
                    "listing_complete": listing_complete,
                },
                {
                    "generation_limit": generation_limit,
                    "evidence_limit": evidence_limit,
                    "time_limit": True,
                },
            )
        scanned_count += 1
        try:
            verified = verify_generation(
                vault,
                profile,
                generation_id=generation_id,
                verify_objects=False,
            )
        except Exception:
            continue
        verified_count += 1
        generation_token = _token(key, "generation", generation_id)
        for reference, entry in _manifest_matches(verified.manifest, missing_by_path):
            if (
                len(evidence[reference["row_id"]]) >= max_evidence_per_reference
                or total_evidence >= max_total_evidence
            ):
                evidence_limit = True
                continue
            normalized = _valid_manifest_evidence(entry)
            if normalized is None:
                continue
            digest, size, object_key = normalized
            expected[reference["path_token"]].add((digest, size))
            posture = "probe_failed"
            exact = False
            try:
                metadata = vault.head(object_key, expected_sha256=digest)
                exact = metadata.sha256 == digest and metadata.size == size
                posture = "verified" if exact else "metadata_mismatch"
            except Exception:
                posture = "unavailable"
            evidence[reference["row_id"]].append(
                {
                    "evidence_class": (
                        "manifest_object_metadata_exact"
                        if exact
                        else "manifest_reference_only"
                    ),
                    "source_token": generation_token,
                    "sha256": digest,
                    "bytes": size,
                    "object_posture": posture,
                }
            )
            total_evidence += 1
    posture = (
        "completed"
        if (
            verified_count == len(generation_ids)
            and listing_complete
            and not generation_limit
            and not evidence_limit
        )
        else "partial"
    )
    return (
        evidence,
        expected,
        posture,
        {
            "returned": len(generation_ids),
            "scanned": scanned_count,
            "verified": verified_count,
            "listing_complete": listing_complete,
        },
        {
            "generation_limit": generation_limit,
            "evidence_limit": evidence_limit,
            "time_limit": False,
        },
    )


def _archive_member_path(value: str) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.removeprefix("./")
    path = _safe_relative_path(normalized)
    return path


def _member_lookup_path(member_path: str) -> str:
    if member_path.startswith("media/"):
        return member_path
    marker = "/media/"
    position = member_path.rfind(marker)
    return member_path[position + 1 :] if position >= 0 else member_path


class _BoundedArchiveReader:
    def __init__(
        self,
        stream: Any,
        *,
        physical_bytes: int,
        compressed: bool,
        max_read_bytes: int,
        read_limit_reason: str,
        max_compression_ratio: int,
        deadline: float,
    ):
        self.stream = stream
        self.physical_bytes = physical_bytes
        self.compressed = compressed
        self.max_read_bytes = max_read_bytes
        self.read_limit_reason = read_limit_reason
        self.max_compression_ratio = max_compression_ratio
        self.deadline = deadline
        self.read_bytes = 0

    def read(self, size: int) -> bytes:
        if time.monotonic() > self.deadline:
            raise CustodyAuditError("archive_time_limit_exceeded")
        data = self.stream.read(size)
        self.read_bytes += len(data)
        if self.read_bytes > self.max_read_bytes:
            raise CustodyAuditError(self.read_limit_reason)
        if (
            self.compressed
            and self.read_bytes
            > max(self.physical_bytes, 1) * self.max_compression_ratio
        ):
            raise CustodyAuditError("archive_compression_ratio_exceeded")
        return data

    def read_exact(self, size: int) -> bytes:
        chunks = []
        remaining = size
        while remaining:
            chunk = self.read(min(READ_CHUNK_BYTES, remaining))
            if not chunk:
                raise CustodyAuditError("archive_member_truncated")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def discard(self, size: int) -> None:
        remaining = size
        while remaining:
            chunk = self.read(min(READ_CHUNK_BYTES, remaining))
            if not chunk:
                raise CustodyAuditError("archive_member_truncated")
            remaining -= len(chunk)


def _tar_octal(field: bytes, reason: str) -> int:
    if field and field[0] & 0x80:
        raise CustodyAuditError(reason)
    value = field.rstrip(b"\0 ").lstrip(b" ")
    if not value:
        return 0
    if any(byte not in b"01234567" for byte in value):
        raise CustodyAuditError(reason)
    return int(value, 8)


def _tar_header(header: bytes) -> tuple[str, int, bytes] | None:
    if len(header) != TAR_BLOCK_BYTES:
        raise CustodyAuditError("archive_header_truncated")
    if header == b"\0" * TAR_BLOCK_BYTES:
        return None
    stored_checksum = _tar_octal(header[148:156], "archive_header_invalid")
    checksum_header = header[:148] + (b" " * 8) + header[156:]
    if sum(checksum_header) != stored_checksum:
        raise CustodyAuditError("archive_header_checksum_invalid")
    typeflag = header[156:157] or b"\0"
    if typeflag in {b"x", b"g", b"X", b"L", b"K", b"S"}:
        raise CustodyAuditError("archive_extended_header_rejected")
    try:
        name = header[0:100].split(b"\0", 1)[0].decode("utf-8")
        prefix = header[345:500].split(b"\0", 1)[0].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CustodyAuditError("archive_member_path_invalid") from exc
    if prefix:
        name = f"{prefix}/{name}"
    size = _tar_octal(header[124:136], "archive_member_size_invalid")
    return name, size, typeflag


def _hash_member(stream: _BoundedArchiveReader, size: int) -> tuple[str, bool]:
    digest = hashlib.sha256()
    first = b""
    remaining = size
    while remaining:
        chunk = stream.read(min(READ_CHUNK_BYTES, remaining))
        if not chunk:
            raise CustodyAuditError("archive_member_truncated")
        if not first:
            first = chunk[:8]
        digest.update(chunk)
        remaining -= len(chunk)
    return digest.hexdigest(), first.startswith(b"%PDF-")


def _scan_archives(
    *,
    archives: list[Path],
    references: list[dict[str, Any]],
    expected: dict[str, set[tuple[str, int]]],
    key: bytes,
    max_members: int,
    max_candidate_bytes: int,
    max_archive_logical_bytes: int,
    max_archive_physical_bytes: int,
    max_archive_read_bytes: int,
    max_total_archive_physical_bytes: int,
    max_total_archive_read_bytes: int,
    max_compression_ratio: int,
    deadline: float,
    max_evidence_per_reference: int,
    max_total_evidence: int,
    initial_evidence_counts: dict[int, int],
    initial_total_evidence: int,
) -> tuple[
    dict[int, list[dict[str, Any]]],
    str,
    list[dict[str, Any]],
    bool,
]:
    evidence: dict[int, list[dict[str, Any]]] = defaultdict(list)
    reference_by_archive_path = {
        f"media/{reference['_path']}": reference for reference in references
    }
    progress = []
    evidence_limit = False
    total_evidence = initial_total_evidence
    total_physical_bytes = 0
    total_read_bytes = 0
    for archive in archives:
        try:
            descriptor, metadata_before = _open_path_no_follow(archive)
        except CustodyAuditError:
            progress.append(
                {
                    "source_token": _token(key, "archive", os.path.abspath(archive)),
                    "posture": "unavailable",
                    "members_scanned": 0,
                    "candidates_hashed": 0,
                    "read_bytes": 0,
                }
            )
            continue
        source_token = _token(key, "archive", os.path.abspath(archive))
        source_progress = {
            "source_token": source_token,
            "posture": "completed",
            "members_scanned": 0,
            "candidates_hashed": 0,
            "read_bytes": 0,
        }
        source_evidence: dict[int, list[dict[str, Any]]] = defaultdict(list)
        source_evidence_count = 0
        reader = None
        try:
            total_physical_bytes += metadata_before.st_size
            if total_physical_bytes > max_total_archive_physical_bytes:
                raise CustodyAuditError(
                    "archive_total_physical_byte_limit_exceeded"
                )
            if metadata_before.st_size > max_archive_physical_bytes:
                raise CustodyAuditError("archive_physical_byte_limit_exceeded")
            with os.fdopen(descriptor, "rb", closefd=False) as archive_stream:
                magic = archive_stream.read(6)
                archive_stream.seek(0)
                compressed = magic.startswith(b"\x1f\x8b")
                if magic.startswith((b"BZh", b"\xfd7zXZ")):
                    raise CustodyAuditError("archive_compression_unsupported")
                payload_stream = (
                    gzip.GzipFile(fileobj=archive_stream, mode="rb")
                    if compressed
                    else archive_stream
                )
                remaining_total_read = (
                    max_total_archive_read_bytes - total_read_bytes
                )
                effective_read_limit = min(
                    max_archive_read_bytes,
                    remaining_total_read,
                )
                reader = _BoundedArchiveReader(
                    payload_stream,
                    physical_bytes=metadata_before.st_size,
                    compressed=compressed,
                    max_read_bytes=effective_read_limit,
                    read_limit_reason=(
                        "archive_total_read_limit_exceeded"
                        if remaining_total_read <= max_archive_read_bytes
                        else "archive_read_limit_exceeded"
                    ),
                    max_compression_ratio=max_compression_ratio,
                    deadline=deadline,
                )
                logical_bytes = 0
                archive_case_paths: dict[str, str] = {}
                archive_paths: set[str] = set()
                while True:
                    header = reader.read_exact(TAR_BLOCK_BYTES)
                    parsed = _tar_header(header)
                    if parsed is None:
                        break
                    member_name, member_size, typeflag = parsed
                    source_progress["members_scanned"] += 1
                    if source_progress["members_scanned"] > max_members:
                        raise CustodyAuditError("archive_member_limit_exceeded")
                    logical_bytes += member_size
                    if logical_bytes > max_archive_logical_bytes:
                        raise CustodyAuditError(
                            "archive_logical_byte_limit_exceeded"
                        )
                    member_path = _archive_member_path(member_name)
                    lookup_path = (
                        _member_lookup_path(member_path)
                        if member_path is not None
                        else ""
                    )
                    if lookup_path:
                        if lookup_path in archive_paths:
                            raise CustodyAuditError("archive_path_duplicate")
                        archive_paths.add(lookup_path)
                        case_key = lookup_path.casefold()
                        previous = archive_case_paths.setdefault(
                            case_key,
                            lookup_path,
                        )
                        if previous != lookup_path:
                            raise CustodyAuditError(
                                "archive_path_case_collision"
                            )
                    reference = reference_by_archive_path.get(lookup_path)
                    is_regular = typeflag in {b"", b"\0", b"0", b"7"}
                    can_record = bool(
                        reference
                        and initial_evidence_counts.get(reference["row_id"], 0)
                        + len(evidence[reference["row_id"]])
                        + len(source_evidence[reference["row_id"]])
                        < max_evidence_per_reference
                        and total_evidence + source_evidence_count
                        < max_total_evidence
                    )
                    if reference and not can_record:
                        evidence_limit = True
                    if not is_regular or reference is None:
                        reader.discard(member_size)
                    elif member_size > max_candidate_bytes:
                        reader.discard(member_size)
                        if can_record:
                            source_evidence[reference["row_id"]].append(
                                {
                                    "evidence_class": "path_only_candidate",
                                    "source_token": source_token,
                                    "sha256": None,
                                    "bytes": member_size,
                                    "pdf_header": "not_checked",
                                    "candidate_posture": "size_limit_exceeded",
                                }
                            )
                            source_evidence_count += 1
                    else:
                        digest, is_pdf = _hash_member(reader, member_size)
                        source_progress["candidates_hashed"] += 1
                        exact = (digest, member_size) in expected.get(
                            reference["path_token"], set()
                        )
                        if can_record:
                            source_evidence[reference["row_id"]].append(
                                {
                                    "evidence_class": (
                                        "exact_manifest_archive"
                                        if exact
                                        else "path_only_candidate"
                                    ),
                                    "source_token": source_token,
                                    "sha256": digest,
                                    "bytes": member_size,
                                    "pdf_header": "valid" if is_pdf else "invalid",
                                    "candidate_posture": (
                                        "verified"
                                        if exact and is_pdf
                                        else "unverified"
                                    ),
                                }
                            )
                            source_evidence_count += 1
                    padding = (-member_size) % TAR_BLOCK_BYTES
                    if padding:
                        reader.discard(padding)
                source_progress["read_bytes"] = reader.read_bytes
                if compressed:
                    payload_stream.close()
            metadata_after = os.fstat(descriptor)
            if _stat_identity(metadata_before) != _stat_identity(metadata_after):
                raise CustodyAuditError("archive_changed_during_audit")
            for row_id, records in source_evidence.items():
                evidence[row_id].extend(records)
            total_evidence += source_evidence_count
        except (CustodyAuditError, OSError, EOFError, gzip.BadGzipFile) as exc:
            source_progress["posture"] = (
                str(exc)
                if isinstance(exc, CustodyAuditError)
                else "archive_read_failed"
            )
        finally:
            if reader is not None:
                source_progress["read_bytes"] = reader.read_bytes
                total_read_bytes += reader.read_bytes
            os.close(descriptor)
        progress.append(source_progress)
    posture = (
        "completed"
        if all(item["posture"] == "completed" for item in progress)
        and not evidence_limit
        else "partial"
    )
    return evidence, posture, progress, evidence_limit


def audit_missing_pdf_custody(
    *,
    database: Path,
    media_root: Path,
    hmac_key: bytes,
    archives: list[Path] | None = None,
    vault: Any = None,
    profile: Any = None,
    list_generation_ids: Any = None,
    verify_generation: Any = None,
    max_pages: int = 100,
    max_archive_members: int = MAX_ARCHIVE_MEMBERS,
    max_candidate_bytes: int = MAX_CANDIDATE_BYTES,
    max_archive_logical_bytes: int = MAX_ARCHIVE_LOGICAL_BYTES,
    max_archive_physical_bytes: int = MAX_ARCHIVE_PHYSICAL_BYTES,
    max_archive_read_bytes: int = MAX_ARCHIVE_READ_BYTES,
    max_total_archive_physical_bytes: int = MAX_TOTAL_ARCHIVE_PHYSICAL_BYTES,
    max_total_archive_read_bytes: int = MAX_TOTAL_ARCHIVE_READ_BYTES,
    max_compression_ratio: int = MAX_COMPRESSION_RATIO,
    max_seconds: int = MAX_AUDIT_SECONDS,
    max_generations: int = MAX_GENERATIONS,
    max_evidence_per_reference: int = MAX_EVIDENCE_PER_REFERENCE,
    max_total_evidence: int = MAX_TOTAL_EVIDENCE,
) -> dict[str, Any]:
    """Audit missing PDF custody without exposing human document metadata."""
    if len(hmac_key) < 32:
        raise CustodyAuditError("hmac_key_too_short")
    archives = archives or []
    if len(archives) > MAX_ARCHIVES:
        raise CustodyAuditError("archive_count_limit_exceeded")
    if max_pages < 1 or max_pages > 100:
        raise CustodyAuditError("generation_page_limit_invalid")
    if max_archive_members < 1 or max_archive_members > MAX_ARCHIVE_MEMBERS:
        raise CustodyAuditError("archive_member_limit_invalid")
    if max_candidate_bytes < 5 or max_candidate_bytes > MAX_CANDIDATE_BYTES:
        raise CustodyAuditError("candidate_byte_limit_invalid")
    if (
        max_archive_logical_bytes < 5
        or max_archive_logical_bytes > MAX_ARCHIVE_LOGICAL_BYTES
    ):
        raise CustodyAuditError("archive_logical_byte_limit_invalid")
    if (
        max_archive_physical_bytes < TAR_BLOCK_BYTES
        or max_archive_physical_bytes > MAX_ARCHIVE_PHYSICAL_BYTES
    ):
        raise CustodyAuditError("archive_physical_byte_limit_invalid")
    if (
        max_archive_read_bytes < TAR_BLOCK_BYTES
        or max_archive_read_bytes > MAX_ARCHIVE_READ_BYTES
    ):
        raise CustodyAuditError("archive_read_limit_invalid")
    if (
        max_total_archive_physical_bytes < TAR_BLOCK_BYTES
        or max_total_archive_physical_bytes > MAX_TOTAL_ARCHIVE_PHYSICAL_BYTES
    ):
        raise CustodyAuditError("archive_total_physical_byte_limit_invalid")
    if (
        max_total_archive_read_bytes < TAR_BLOCK_BYTES
        or max_total_archive_read_bytes > MAX_TOTAL_ARCHIVE_READ_BYTES
    ):
        raise CustodyAuditError("archive_total_read_limit_invalid")
    if max_compression_ratio < 1 or max_compression_ratio > MAX_COMPRESSION_RATIO:
        raise CustodyAuditError("archive_compression_ratio_limit_invalid")
    if max_seconds < 1 or max_seconds > MAX_AUDIT_SECONDS:
        raise CustodyAuditError("audit_time_limit_invalid")
    if max_generations < 1 or max_generations > MAX_GENERATIONS:
        raise CustodyAuditError("generation_limit_invalid")
    if (
        max_evidence_per_reference < 1
        or max_evidence_per_reference > MAX_EVIDENCE_PER_REFERENCE
    ):
        raise CustodyAuditError("evidence_per_reference_limit_invalid")
    if max_total_evidence < 1 or max_total_evidence > MAX_TOTAL_EVIDENCE:
        raise CustodyAuditError("total_evidence_limit_invalid")

    deadline = time.monotonic() + max_seconds
    references = missing_pdf_references(database, media_root, hmac_key)
    initial_time_limit = time.monotonic() > deadline
    evidence: dict[int, list[dict[str, Any]]] = defaultdict(list)
    expected: dict[str, set[tuple[str, int]]] = defaultdict(set)
    vault_posture = "not_requested"
    vault_generation_counts = {
        "returned": 0,
        "scanned": 0,
        "verified": 0,
        "listing_complete": False,
    }
    truncation = {
        "generation_limit": False,
        "evidence_limit": False,
        "time_limit": initial_time_limit,
    }
    if vault is not None and profile is not None:
        if list_generation_ids is None or verify_generation is None:
            raise CustodyAuditError("vault_audit_dependency_missing")
        (
            vault_evidence,
            expected,
            vault_posture,
            vault_generation_counts,
            vault_truncation,
        ) = _scan_vault(
            vault=vault,
            profile=profile,
            references=references,
            key=hmac_key,
            list_generation_ids=list_generation_ids,
            verify_generation=verify_generation,
            max_pages=max_pages,
            max_generations=max_generations,
            max_evidence_per_reference=max_evidence_per_reference,
            max_total_evidence=max_total_evidence,
            deadline=deadline,
        )
        for name, value in vault_truncation.items():
            truncation[name] = truncation[name] or value
        for row_id, records in vault_evidence.items():
            evidence[row_id].extend(records)

    archive_posture = "not_requested"
    archive_progress: list[dict[str, Any]] = []
    if archives:
        initial_evidence_counts = {
            row_id: len(records) for row_id, records in evidence.items()
        }
        initial_total_evidence = sum(initial_evidence_counts.values())
        (
            archive_evidence,
            archive_posture,
            archive_progress,
            archive_evidence_limit,
        ) = _scan_archives(
            archives=archives,
            references=references,
            expected=expected,
            key=hmac_key,
            max_members=max_archive_members,
            max_candidate_bytes=max_candidate_bytes,
            max_archive_logical_bytes=max_archive_logical_bytes,
            max_archive_physical_bytes=max_archive_physical_bytes,
            max_archive_read_bytes=max_archive_read_bytes,
            max_total_archive_physical_bytes=max_total_archive_physical_bytes,
            max_total_archive_read_bytes=max_total_archive_read_bytes,
            max_compression_ratio=max_compression_ratio,
            deadline=deadline,
            max_evidence_per_reference=max_evidence_per_reference,
            max_total_evidence=max_total_evidence,
            initial_evidence_counts=initial_evidence_counts,
            initial_total_evidence=initial_total_evidence,
        )
        truncation["evidence_limit"] = (
            truncation["evidence_limit"] or archive_evidence_limit
        )
        truncation["time_limit"] = truncation["time_limit"] or any(
            item["posture"] == "archive_time_limit_exceeded"
            for item in archive_progress
        )
        for row_id, records in archive_evidence.items():
            evidence[row_id].extend(records)

    results = []
    class_counts: Counter[str] = Counter()
    for reference in references:
        records = evidence.get(reference["row_id"], [])
        if not records:
            records = [{"evidence_class": "no_match"}]
        for record in records:
            class_counts[record["evidence_class"]] += 1
        results.append(
            {
                "row_id": reference["row_id"],
                "path_token": reference["path_token"],
                "evidence": records,
            }
        )
    return {
        "schema": AUDIT_SCHEMA,
        "read_only": True,
        "redaction": "hmac-sha256",
        "vault_posture": vault_posture,
        "vault_generation_counts": vault_generation_counts,
        "archive_posture": archive_posture,
        "archive_progress": archive_progress,
        "truncation": truncation,
        "complete": (
            vault_posture in {"not_requested", "completed"}
            and archive_posture in {"not_requested", "completed"}
            and not any(truncation.values())
        ),
        "missing_reference_count": len(references),
        "evidence_counts": {
            name: class_counts.get(name, 0)
            for name in sorted(EVIDENCE_CLASSES)
        },
        "results": results,
    }
