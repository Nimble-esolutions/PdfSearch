"""Redacted, read-only custody discovery for missing PDF references."""

from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
import stat
import tarfile
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import quote


AUDIT_SCHEMA = "pdfsearch-missing-pdf-custody/v1"
EVIDENCE_CLASSES = {
    "exact_manifest_object",
    "manifest_reference_only",
    "exact_manifest_archive",
    "path_only_candidate",
    "no_match",
}
MAX_ARCHIVES = 32
MAX_ARCHIVE_MEMBERS = 250_000
MAX_CANDIDATE_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_LOGICAL_BYTES = 20 * 1024 * 1024 * 1024
READ_CHUNK_BYTES = 1024 * 1024


class CustodyAuditError(RuntimeError):
    """Stable, secret-free custody-audit failure."""


def _token(key: bytes, kind: str, value: str) -> str:
    digest = hmac.new(
        key,
        f"{kind}\0{value}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"hmac-sha256:{digest}"


def _sqlite_uri(path: Path) -> str:
    return f"file:{quote(str(path), safe='/')}?mode=ro"


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


def _local_pdf_path(media_root: Path, stored_path: str) -> Path | None:
    root = media_root.resolve()
    candidate = (root / stored_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def missing_pdf_references(database: Path, media_root: Path, key: bytes) -> list[dict[str, Any]]:
    """Return only IDs, HMAC path tokens, and internal paths for missing rows."""
    if not database.is_file():
        raise CustodyAuditError("application_database_unavailable")
    try:
        connection = sqlite3.connect(_sqlite_uri(database), uri=True)
        connection.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
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
        for row in connection.execute(query):
            stored_path = _stored_path(row, columns)
            if not stored_path:
                continue
            local_path = _local_pdf_path(media_root, stored_path)
            if local_path is not None and local_path.is_file():
                continue
            missing.append(
                {
                    "row_id": int(row["id"]),
                    "path_token": _token(key, "path", stored_path.casefold()),
                    "_path": stored_path,
                }
            )
        return missing
    except sqlite3.Error as exc:
        raise CustodyAuditError("application_database_read_failed") from exc
    finally:
        connection.close()


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
    for entry in files:
        if not isinstance(entry, dict):
            continue
        path = _manifest_path(entry.get("path"))
        if path is None:
            continue
        reference = missing_by_path.get(path.casefold())
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
) -> tuple[
    dict[int, list[dict[str, Any]]],
    dict[str, set[tuple[str, int]]],
    str,
    dict[str, int],
]:
    evidence: dict[int, list[dict[str, Any]]] = defaultdict(list)
    expected: dict[str, set[tuple[str, int]]] = defaultdict(set)
    missing_by_path = {item["_path"].casefold(): item for item in references}
    try:
        generation_ids = list_generation_ids(vault, profile, max_pages=max_pages)
    except Exception:
        return evidence, expected, "unavailable", {"listed": 0, "verified": 0}
    verified_count = 0
    for generation_id in generation_ids:
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
                        "exact_manifest_object" if exact else "manifest_reference_only"
                    ),
                    "source_token": generation_token,
                    "sha256": digest,
                    "bytes": size,
                    "object_posture": posture,
                }
            )
    posture = "completed" if verified_count == len(generation_ids) else "partial"
    return (
        evidence,
        expected,
        posture,
        {"listed": len(generation_ids), "verified": verified_count},
    )


def _archive_member_path(value: str) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.removeprefix("./")
    path = _safe_relative_path(normalized)
    return path


def _member_reference(
    member_path: str,
    references: list[dict[str, Any]],
) -> dict[str, Any] | None:
    casefolded = member_path.casefold()
    for reference in references:
        stored = reference["_path"].casefold()
        expected = f"media/{stored}"
        if casefolded == expected or casefolded.endswith(f"/{expected}"):
            return reference
    return None


def _hash_member(stream: Any, size: int) -> tuple[str, bool]:
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
) -> tuple[dict[int, list[dict[str, Any]]], str]:
    evidence: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for archive in archives:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = None
        try:
            descriptor = os.open(archive, flags)
            metadata = os.fstat(descriptor)
        except OSError as exc:
            if descriptor is not None:
                os.close(descriptor)
            raise CustodyAuditError("archive_unavailable") from exc
        if not stat.S_ISREG(metadata.st_mode):
            os.close(descriptor)
            raise CustodyAuditError("archive_unavailable")
        source_token = _token(key, "archive", str(archive.resolve()))
        with os.fdopen(descriptor, "rb") as archive_stream:
            try:
                bundle = tarfile.open(fileobj=archive_stream, mode="r:*")
            except (OSError, tarfile.TarError) as exc:
                raise CustodyAuditError("archive_unreadable") from exc
            with bundle:
                logical_bytes = 0
                for index, member in enumerate(bundle):
                    if index >= max_members:
                        raise CustodyAuditError("archive_member_limit_exceeded")
                    if not member.isfile():
                        continue
                    if member.size < 0:
                        raise CustodyAuditError("archive_member_invalid")
                    logical_bytes += member.size
                    if logical_bytes > max_archive_logical_bytes:
                        raise CustodyAuditError(
                            "archive_logical_byte_limit_exceeded"
                        )
                    member_path = _archive_member_path(member.name)
                    if member_path is None:
                        continue
                    reference = _member_reference(member_path, references)
                    if reference is None:
                        continue
                    if member.size > max_candidate_bytes:
                        evidence[reference["row_id"]].append(
                            {
                                "evidence_class": "path_only_candidate",
                                "source_token": source_token,
                                "sha256": None,
                                "bytes": member.size,
                                "pdf_header": "not_checked",
                                "candidate_posture": "size_limit_exceeded",
                            }
                        )
                        continue
                    member_stream = bundle.extractfile(member)
                    if member_stream is None:
                        continue
                    with member_stream:
                        digest, is_pdf = _hash_member(member_stream, member.size)
                    exact = (digest, member.size) in expected.get(
                        reference["path_token"], set()
                    )
                    evidence[reference["row_id"]].append(
                        {
                            "evidence_class": (
                                "exact_manifest_archive"
                                if exact
                                else "path_only_candidate"
                            ),
                            "source_token": source_token,
                            "sha256": digest,
                            "bytes": member.size,
                            "pdf_header": "valid" if is_pdf else "invalid",
                            "candidate_posture": (
                                "verified" if exact and is_pdf else "unverified"
                            ),
                        }
                    )
    return evidence, "completed"


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

    references = missing_pdf_references(database, media_root, hmac_key)
    evidence: dict[int, list[dict[str, Any]]] = defaultdict(list)
    expected: dict[str, set[tuple[str, int]]] = defaultdict(set)
    vault_posture = "not_requested"
    vault_generation_counts = {"listed": 0, "verified": 0}
    if vault is not None and profile is not None:
        if list_generation_ids is None or verify_generation is None:
            raise CustodyAuditError("vault_audit_dependency_missing")
        (
            vault_evidence,
            expected,
            vault_posture,
            vault_generation_counts,
        ) = _scan_vault(
            vault=vault,
            profile=profile,
            references=references,
            key=hmac_key,
            list_generation_ids=list_generation_ids,
            verify_generation=verify_generation,
            max_pages=max_pages,
        )
        for row_id, records in vault_evidence.items():
            evidence[row_id].extend(records)

    archive_posture = "not_requested"
    if archives:
        archive_evidence, archive_posture = _scan_archives(
            archives=archives,
            references=references,
            expected=expected,
            key=hmac_key,
            max_members=max_archive_members,
            max_candidate_bytes=max_candidate_bytes,
            max_archive_logical_bytes=max_archive_logical_bytes,
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
        "missing_reference_count": len(references),
        "evidence_counts": {
            name: class_counts.get(name, 0)
            for name in sorted(EVIDENCE_CLASSES)
        },
        "results": results,
    }
