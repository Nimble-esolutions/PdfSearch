"""Fail-closed checks for a declared database seed and its media files."""

from __future__ import annotations

import sqlite3
from pathlib import Path


class RuntimeDataGateError(RuntimeError):
    """Raised when a declared seed cannot provide its referenced PDF files."""


def _safe_media_path(media_root: Path, stored_path: str | None) -> Path | None:
    if not stored_path:
        return None
    candidate = Path(stored_path)
    if candidate.is_absolute():
        return None
    resolved_root = media_root.resolve()
    resolved = (media_root / candidate).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError:
        return None
    return resolved


def seed_pdf_media_report(database_path: str | Path, media_root: str | Path) -> dict[str, object]:
    """Inspect PDF rows in a seed without writing to either path."""
    database = Path(database_path).expanduser().resolve()
    media = Path(media_root).expanduser().resolve()
    if not database.is_file():
        raise RuntimeDataGateError(f"Declared seed database is missing: {database}")

    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise RuntimeDataGateError("Declared seed database cannot be opened") from exc

    missing: list[dict[str, object]] = []
    try:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='core_pdffile'"
        ).fetchone()
        if not table:
            return {"pdf_rows": 0, "missing_media": []}
        rows = connection.execute('SELECT id, file FROM "core_pdffile" ORDER BY id').fetchall()
        for pdf_id, stored_path in rows:
            resolved = _safe_media_path(media, stored_path)
            if resolved is None or not resolved.is_file():
                missing.append({"db_id": pdf_id, "path": stored_path})
        return {"pdf_rows": len(rows), "missing_media": missing}
    except sqlite3.Error as exc:
        raise RuntimeDataGateError("Declared seed PDF rows cannot be inspected") from exc
    finally:
        connection.close()


def validate_seed_pdf_media(database_path: str | Path, media_root: str | Path) -> dict[str, object]:
    report = seed_pdf_media_report(database_path, media_root)
    missing = report["missing_media"]
    if missing:
        raise RuntimeDataGateError(
            f"Declared seed contains {report['pdf_rows']} PDF row(s) but "
            f"{len(missing)} referenced media file(s) are missing or unsafe"
        )
    return report
