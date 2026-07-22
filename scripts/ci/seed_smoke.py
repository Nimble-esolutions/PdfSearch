"""Validate the image's declared seed path without copying production media."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

import sys

sys.path.insert(0, "/app/flowdocs")

from core.runtime_data_gate import RuntimeDataGateError, seed_pdf_media_report, validate_seed_pdf_media


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    database = Path(os.environ.get("DECLARED_SEED_DB", "/app/init/db.sqlite3")).resolve()
    require(database.is_file(), f"declared seed database is missing: {database}")

    with tempfile.TemporaryDirectory(prefix="pdfsearch-seed-smoke-") as temporary:
        media_root = Path(temporary) / "media"
        report = seed_pdf_media_report(database, media_root)
        require(report["pdf_rows"] > 0, "declared seed has no PDF rows to validate")
        require(
            len(report["missing_media"]) == report["pdf_rows"],
            "seed smoke fixture unexpectedly found media before creating placeholders",
        )
        try:
            validate_seed_pdf_media(database, media_root)
        except RuntimeDataGateError:
            pass
        else:
            raise AssertionError("seed gate accepted PDF rows without media")

        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
            rows = connection.execute('SELECT file FROM "core_pdffile" ORDER BY id').fetchall()
        for (stored_path,) in rows:
            path = Path(stored_path)
            require(not path.is_absolute() and ".." not in path.parts, f"unsafe seed path: {stored_path}")
            target = media_root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"disposable seed smoke placeholder")

        validated = validate_seed_pdf_media(database, media_root)
        require(validated["missing_media"] == [], "seed gate rejected complete disposable media")
        print(
            "[seed] declared init path passed missing-media rejection and disposable recovery "
            f"checks: pdf_rows={report['pdf_rows']}"
        )


if __name__ == "__main__":
    main()
