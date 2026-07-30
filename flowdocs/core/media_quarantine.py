"""Secret-free, deterministic evidence for unavailable document records."""

from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath


STORAGE_KEY_STATUSES = {"blank", "present", "unsafe"}
PRIOR_LIFECYCLES = {"uploaded", "processing", "ready", "deprecated", "archived"}
MAX_ATTESTED_DOCUMENTS = 10_000_000
MAX_EXPECTED_SIZE = 2**63 - 1


def storage_key_evidence(value) -> dict:
    key = str(value or "")
    if not key:
        status = "blank"
        normalized = ""
    else:
        path = PurePosixPath(key)
        ambiguous = (
            key != key.strip()
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
