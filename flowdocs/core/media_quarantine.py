"""Secret-free, deterministic evidence for unavailable document records."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import time
import unicodedata
from pathlib import Path, PurePosixPath


STORAGE_KEY_STATUSES = {"blank", "present", "unsafe"}
PRIOR_LIFECYCLES = {"uploaded", "processing", "ready", "deprecated", "archived"}
MAX_ATTESTED_DOCUMENTS = 10_000_000
MAX_EXPECTED_SIZE = 2**63 - 1
MAX_DIRECTORY_ENTRIES = 100_000


class MediaFileAbsentError(FileNotFoundError):
    """The canonical storage entry is definitively absent."""


class MediaFileUnsafeError(OSError):
    """The storage entry cannot be proved as one stable regular file."""


def storage_key_evidence(value) -> dict:
    key = str(value or "")
    if not key:
        status = "blank"
        normalized = ""
    else:
        path = PurePosixPath(key)
        ambiguous = (
            key != key.strip()
            or key != path.as_posix()
            or key != unicodedata.normalize("NFC", key)
            or "\\" in key
            or any(ord(character) < 32 or ord(character) == 127 for character in key)
        )
        status = (
            "unsafe"
            if ambiguous
            or path.is_absolute()
            or ".." in path.parts
            or any(part in {"", "."} for part in path.parts)
            else "present"
        )
        normalized = path.as_posix()
    return {
        "status": status,
        "token_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
    }


def storage_key_collision_token(value) -> str:
    evidence = storage_key_evidence(value)
    if evidence["status"] != "present":
        raise ValueError("storage key is not canonical")
    return unicodedata.normalize("NFC", str(value)).casefold()


def _same_file_identity(*records) -> bool:
    fields = ("st_dev", "st_ino", "st_mode")
    return all(
        all(
            getattr(records[0], field) == getattr(record, field)
            for field in fields
        )
        for record in records[1:]
    )


def verify_local_media_file(
    root,
    storage_key,
    *,
    maximum_bytes,
    deadline_seconds=30,
    hash_content=True,
) -> dict:
    """Hash one canonical regular file through stable no-follow descriptors."""
    evidence = storage_key_evidence(storage_key)
    if evidence["status"] != "present":
        raise MediaFileUnsafeError("storage key is unsafe")
    relative = PurePosixPath(str(storage_key))
    descriptors = []
    component_records = []
    try:
        if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
            raise MediaFileUnsafeError("no-follow file verification is unsupported")
        root_flags = (
            os.O_RDONLY
            | os.O_DIRECTORY
            | os.O_NOFOLLOW
        )
        parent = os.open(Path(root), root_flags)
        descriptors.append(parent)
        for component in relative.parts[:-1]:
            with os.scandir(parent) as entries:
                names = []
                for entry_number, entry in enumerate(entries, start=1):
                    if entry_number > MAX_DIRECTORY_ENTRIES:
                        raise MediaFileUnsafeError("directory entry bound exceeded")
                    names.append(entry.name)
            if component not in names:
                raise MediaFileAbsentError("media directory is absent")
            if any(
                name != component
                and unicodedata.normalize("NFC", name).casefold()
                == unicodedata.normalize("NFC", component).casefold()
                for name in names
            ):
                raise MediaFileUnsafeError("directory component is ambiguous")
            before = os.stat(component, dir_fd=parent, follow_symlinks=False)
            child = os.open(component, root_flags, dir_fd=parent)
            descriptors.append(child)
            opened = os.fstat(child)
            after = os.stat(component, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISDIR(opened.st_mode) or not _same_file_identity(
                before, opened, after
            ):
                raise MediaFileUnsafeError("directory identity changed")
            component_records.append((parent, component, opened))
            parent = child
        filename = relative.parts[-1]
        with os.scandir(parent) as entries:
            names = []
            for entry_number, entry in enumerate(entries, start=1):
                if entry_number > MAX_DIRECTORY_ENTRIES:
                    raise MediaFileUnsafeError("directory entry bound exceeded")
                names.append(entry.name)
        if filename not in names:
            raise MediaFileAbsentError("media entry is absent")
        if any(
            name != filename
            and unicodedata.normalize("NFC", name).casefold()
            == unicodedata.normalize("NFC", filename).casefold()
            for name in names
        ):
            raise MediaFileUnsafeError("media entry is ambiguous")
        before = os.stat(filename, dir_fd=parent, follow_symlinks=False)
        descriptor = os.open(
            filename,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=parent,
        )
        descriptors.append(descriptor)
        opened = os.fstat(descriptor)
        after_open = os.stat(filename, dir_fd=parent, follow_symlinks=False)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or not _same_file_identity(before, opened, after_open)
            or opened.st_size > maximum_bytes
        ):
            raise MediaFileUnsafeError("media is not an approved regular file")
        deadline = time.monotonic() + deadline_seconds
        digest = hashlib.sha256() if hash_content else None
        size = 0
        if hash_content:
            while True:
                if time.monotonic() > deadline:
                    raise MediaFileUnsafeError(
                        "media verification deadline exceeded"
                    )
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
                if size > opened.st_size or size > maximum_bytes:
                    raise MediaFileUnsafeError("media changed size while verified")
        else:
            size = opened.st_size
        after_read = os.fstat(descriptor)
        after_path = os.stat(
            filename,
            dir_fd=parent,
            follow_symlinks=False,
        )
        stable_fields = (
            "st_dev",
            "st_ino",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if (
            not _same_file_identity(opened, after_read, after_path)
            or any(
                getattr(opened, field) != getattr(after_read, field)
                or getattr(opened, field) != getattr(after_path, field)
                for field in stable_fields
            )
            or size != opened.st_size
        ):
            raise MediaFileUnsafeError("media changed while verified")
        for ancestor_fd, component, opened_directory in component_records:
            current = os.stat(
                component,
                dir_fd=ancestor_fd,
                follow_symlinks=False,
            )
            if not _same_file_identity(opened_directory, current):
                raise MediaFileUnsafeError("directory mapping changed")
        return {
            "sha256": digest.hexdigest() if digest is not None else None,
            "size": size,
        }
    except MediaFileUnsafeError:
        raise
    except FileNotFoundError as exc:
        raise MediaFileAbsentError("media is absent") from exc
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise MediaFileUnsafeError("media could not be verified") from exc
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def storage_key_status(value) -> str:
    return storage_key_evidence(value)["status"]


def build_unavailable_attestation(records, *, id_limit=20) -> dict:
    if not isinstance(id_limit, int) or isinstance(id_limit, bool) or not 0 <= id_limit <= 100:
        raise ValueError("id_limit must be between 0 and 100")
    digest = hashlib.sha256()
    digest.update(b"[")
    identifiers = []
    count = 0
    previous_id = 0
    for record in records:
        identifier = record["id"]
        lifecycle = str(record.get("lifecycle") or "")
        status = str(record["storage_key_status"])
        storage_token = str(record["storage_key_token_sha256"])
        expected_sha256 = str(record.get("expected_sha256") or "")
        expected_size = record.get("expected_size")
        prior_lifecycle = str(record.get("prior_lifecycle") or "")
        if (
            not isinstance(identifier, int)
            or isinstance(identifier, bool)
            or identifier <= previous_id
            or lifecycle != "unavailable"
            or status not in STORAGE_KEY_STATUSES
            or len(storage_token) != 64
            or any(character not in "0123456789abcdef" for character in storage_token)
            or (
                expected_sha256
                and (
                    len(expected_sha256) != 64
                    or any(
                        character not in "0123456789abcdef"
                        for character in expected_sha256
                    )
                )
            )
            or not (
                (
                    expected_sha256
                    and isinstance(expected_size, int)
                    and not isinstance(expected_size, bool)
                    and 0 <= expected_size <= MAX_EXPECTED_SIZE
                    and prior_lifecycle in PRIOR_LIFECYCLES
                )
                or (
                    not expected_sha256
                    and expected_size is None
                    and (
                        not prior_lifecycle
                        or prior_lifecycle in PRIOR_LIFECYCLES
                    )
                )
            )
        ):
            raise ValueError("unavailable attestation record is invalid")
        canonical = (
            identifier,
            lifecycle,
            status,
            storage_token,
            expected_sha256,
            expected_size,
            prior_lifecycle,
        )
        if count:
            digest.update(b",")
        digest.update(
            json.dumps(
                canonical,
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("ascii")
        )
        if len(identifiers) < id_limit:
            identifiers.append(identifier)
        count += 1
        if count > MAX_ATTESTED_DOCUMENTS:
            raise ValueError("unavailable attestation exceeds the approved bound")
        previous_id = identifier
    digest.update(b"]")
    return {
        "count": count,
        "ids": identifiers,
        "truncated": count > id_limit,
        "preview_limit": id_limit,
        "set_sha256": digest.hexdigest(),
    }


def build_unauthorized_missing_attestation(records, *, id_limit=20) -> dict:
    """Bind every absent row whose lifecycle is not unavailable."""
    if not isinstance(id_limit, int) or isinstance(id_limit, bool) or not 0 <= id_limit <= 100:
        raise ValueError("id_limit must be between 0 and 100")
    digest = hashlib.sha256()
    digest.update(b"[")
    identifiers = []
    count = 0
    previous_id = 0
    for record in records:
        identifier = record["id"]
        lifecycle = record.get("lifecycle")
        file_status = record.get("file_status")
        storage_token = record.get("storage_key_token_sha256")
        if (
            not isinstance(identifier, int)
            or isinstance(identifier, bool)
            or identifier <= previous_id
            or lifecycle == "unavailable"
            or not isinstance(lifecycle, str)
            or not lifecycle
            or file_status not in {"missing", "missing-path", "unsafe-path"}
            or not isinstance(storage_token, str)
            or len(storage_token) != 64
            or any(character not in "0123456789abcdef" for character in storage_token)
        ):
            raise ValueError("unauthorized missing-media record is invalid")
        canonical = (identifier, lifecycle, file_status, storage_token)
        if count:
            digest.update(b",")
        digest.update(
            json.dumps(canonical, ensure_ascii=True, separators=(",", ":")).encode(
                "ascii"
            )
        )
        if len(identifiers) < id_limit:
            identifiers.append(identifier)
        count += 1
        previous_id = identifier
    digest.update(b"]")
    return {
        "count": count,
        "ids": identifiers,
        "truncated": count > id_limit,
        "preview_limit": id_limit,
        "set_sha256": digest.hexdigest(),
    }


def validate_unavailable_attestation(value) -> dict:
    if not isinstance(value, dict):
        raise ValueError("unavailable attestation must be an object")
    count = value.get("count")
    identifiers = value.get("ids")
    truncated = value.get("truncated")
    preview_limit = value.get("preview_limit")
    digest = value.get("set_sha256")
    if (
        not isinstance(count, int)
        or isinstance(count, bool)
        or count < 0
        or not isinstance(identifiers, list)
        or len(identifiers) > 100
        or not isinstance(preview_limit, int)
        or isinstance(preview_limit, bool)
        or not 0 <= preview_limit <= 100
        or len(identifiers) > preview_limit
        or any(
            not isinstance(identifier, int) or identifier <= 0
            for identifier in identifiers
        )
        or identifiers != sorted(set(identifiers))
        or not isinstance(truncated, bool)
        or count < len(identifiers)
        or truncated != (count > preview_limit)
        or len(identifiers) != min(count, preview_limit)
        or not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise ValueError("unavailable attestation is invalid")
    return {
        "count": count,
        "ids": identifiers,
        "truncated": truncated,
        "preview_limit": preview_limit,
        "set_sha256": digest,
    }
