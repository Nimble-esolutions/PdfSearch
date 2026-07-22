"""Run a read-only data-release and index compatibility gate on CI fixtures."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "/app/flowdocs")

import django


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "flowdocs.settings")
    django.setup()

    from core.data_release_validation import validate_release
    from core.management.commands.inventory_artifacts import build_manifest

    root = Path(os.environ.get("DATA_ROOT", "/app/data")).resolve()
    database = root / "db.sqlite3"
    before_database = hashlib.sha256(database.read_bytes()).hexdigest()
    manifest = build_manifest(root)
    faiss_files = manifest["faiss"]["files"]
    dimensions = sorted(
        {
            item["faiss"]["dimensions"]
            for item in faiss_files
            if item.get("faiss", {}).get("loadable")
        }
    )
    vectors = sum(
        item["faiss"]["vector_count"]
        for item in faiss_files
        if item.get("faiss", {}).get("loadable")
    )
    if dimensions != [2] or vectors < 1:
        raise AssertionError(f"index compatibility failed: dimensions={dimensions} vectors={vectors}")

    counts = manifest["counts"]
    manifest["expected_counts"] = {
        "pdf_rows": counts["pdf_rows"],
        "pdf_storage_files": counts["pdf_storage_files"],
        "faiss_files": counts["faiss_files"],
        "faiss_vectors": vectors,
        "faiss_dimension": 2,
        "preserved_target_only_rows": counts["pdf_rows_missing_files"],
    }
    report = validate_release(manifest, root)
    if not report["ok"]:
        print(json.dumps({"issues": report["issues"]}, indent=2, sort_keys=True), file=sys.stderr)
        raise AssertionError("data-release validation failed")

    after_database = hashlib.sha256(database.read_bytes()).hexdigest()
    if before_database != after_database:
        raise AssertionError("data-release validation modified SQLite")

    manifest_path = Path("/tmp/ci-data-release-manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        "[data-release] validation passed "
        f"pdf_rows={counts['pdf_rows']} pdf_files={counts['pdf_storage_files']} "
        f"faiss_files={counts['faiss_files']} faiss_vectors={vectors}"
    )
    print(f"[index-compatibility] loadable_dimensions={dimensions} expected=2")
    print(f"[data-release] manifest={manifest_path}")


if __name__ == "__main__":
    main()
