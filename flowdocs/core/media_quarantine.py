"""Secret-free, deterministic evidence for unavailable document records."""

from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath


def storage_key_status(value) -> str:
    key = str(value or "").strip().replace("\\", "/")
    if not key:
        return "blank"
    path = PurePosixPath(key)
    if path.is_absolute() or ".." in path.parts:
        return "unsafe"
    return "present"


def build_unavailable_attestation(records, *, id_limit=20) -> dict:
    canonical = sorted(
        (
            int(record["id"]),
            str(record.get("lifecycle") or ""),
            str(record["storage_key_status"]),
        )
        for record in records
    )
    identifiers = [record[0] for record in canonical]
    encoded = json.dumps(
        canonical,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    return {
        "count": len(canonical),
        "ids": identifiers[:id_limit],
        "truncated": len(identifiers) > id_limit,
        "set_sha256": hashlib.sha256(encoded).hexdigest(),
    }
