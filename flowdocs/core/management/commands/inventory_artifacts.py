"""Read-only inventory and comparison of PdfSearch data artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


INVENTORY_SCHEMA = "pdfsearch-artifact-inventory/v1"
MANIFEST_VERSION = 1
_METADATA_SUFFIXES = {".json", ".jsonl", ".yaml", ".yml", ".npy", ".npz", ".pkl", ".pickle"}
_METADATA_NAME = re.compile(r"(?:embedding|metadata|index)", re.IGNORECASE)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_record(root: Path, path: Path) -> dict[str, Any]:
    relative = path.relative_to(root).as_posix()
    return {
        "path": relative,
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _iter_files(root: Path) -> Iterable[Path]:
    if not root.is_dir():
        return
    for directory, directories, files in os.walk(root, followlinks=False):
        directories.sort()
        for filename in sorted(files):
            path = Path(directory) / filename
            if path.is_file():
                yield path


def _safe_storage_path(media_root: Path, stored_path: str | None) -> Path | None:
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


def _json_shape(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, (list, dict)) else None
    return None


def _embedding_metadata(value: Any) -> dict[str, Any]:
    parsed = _json_shape(value)
    if not isinstance(parsed, list):
        return {"status": "empty" if not parsed else "invalid", "count": 0, "dimensions": []}

    dimensions = sorted(
        {
            len(item)
            for item in parsed
            if isinstance(item, (list, tuple))
        }
    )
    return {
        "status": "available" if parsed else "empty",
        "count": len(parsed),
        "dimensions": dimensions,
    }


def _chunk_count(value: Any) -> int:
    parsed = _json_shape(value)
    return len(parsed) if isinstance(parsed, list) else 0


def _sqlite_uri(path: Path) -> str:
    return f"file:{quote(str(path), safe='/')}?mode=ro"


def _table_info(connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    escaped = table.replace('"', '""')
    return [
        {
            "name": row[1],
            "type": row[2],
            "notnull": bool(row[3]),
            "primary_key": row[5],
        }
        for row in connection.execute(f'PRAGMA table_info("{escaped}")')
    ]


def _path_contract(root: Path, configured_path: Path) -> tuple[str, str]:
    resolved = configured_path.expanduser().resolve()
    try:
        return "in-contract", resolved.relative_to(root).as_posix()
    except ValueError:
        return "out-of-contract", str(resolved)


def _tree_inventory(root: Path, configured_path: Path) -> dict[str, Any]:
    contract, path_value = _path_contract(root, configured_path)
    result: dict[str, Any] = {
        "root": path_value,
        "contract": contract,
        "exists": configured_path.is_dir(),
        "files": [],
        "file_count": None if contract == "out-of-contract" else 0,
    }
    if contract == "out-of-contract":
        result["reason"] = "configured tree is outside the declared data root"
        return result
    files = [_file_record(root, path) for path in _iter_files(configured_path)]
    result["files"] = sorted(files, key=lambda item: item["path"])
    result["file_count"] = len(files)
    return result


def _sqlite_inventory(database_path: Path, root: Path) -> dict[str, Any]:
    contract, path_value = _path_contract(root, database_path)
    result: dict[str, Any] = {
        "path": path_value,
        "contract": contract,
        "exists": database_path.is_file(),
        "size_bytes": None,
        "sha256": None,
        "sqlite": {
            "application_id": None,
            "user_version": None,
            "tables": [],
            "table_count": 0,
        },
        "migrations": {"applied": [], "count": 0, "latest": None},
    }
    if not result["exists"]:
        if contract == "out-of-contract":
            result["reason"] = "configured database is outside the declared data root"
        return result

    result["size_bytes"] = database_path.stat().st_size
    result["sha256"] = _sha256(database_path)
    try:
        connection = sqlite3.connect(_sqlite_uri(database_path), uri=True)
    except sqlite3.Error as exc:
        result["error"] = f"sqlite-open-error: {exc.__class__.__name__}"
        return result

    try:
        application_id = connection.execute("PRAGMA application_id").fetchone()[0]
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        table_names = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        tables = []
        for table in table_names:
            escaped = table.replace('"', '""')
            row_count = connection.execute(f'SELECT COUNT(*) FROM "{escaped}"').fetchone()[0]
            tables.append({"name": table, "columns": _table_info(connection, table), "row_count": row_count})
        result["sqlite"] = {
            "application_id": application_id,
            "user_version": user_version,
            "tables": tables,
            "table_count": len(tables),
        }

        if "django_migrations" in table_names:
            migration_rows = connection.execute(
                "SELECT app, name FROM django_migrations ORDER BY app, name"
            ).fetchall()
            applied = [{"app": row[0], "name": row[1]} for row in migration_rows]
            core_migrations = [row["name"] for row in applied if row["app"] == "core"]
            result["migrations"] = {
                "applied": applied,
                "count": len(applied),
                "latest": core_migrations[-1] if core_migrations else None,
            }
    except sqlite3.Error as exc:
        result["error"] = f"sqlite-read-error: {exc.__class__.__name__}"
    finally:
        connection.close()
    return result


def _pdf_rows(database_path: Path, media_root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not database_path.is_file():
        return [], {"available": False, "columns": [], "row_count": 0}
    try:
        connection = sqlite3.connect(_sqlite_uri(database_path), uri=True)
        connection.row_factory = sqlite3.Row
    except sqlite3.Error:
        return [], {"available": False, "columns": [], "row_count": 0}

    try:
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if "core_pdffile" not in table_names:
            return [], {"available": False, "columns": [], "row_count": 0}
        columns = [row[1] for row in connection.execute('PRAGMA table_info("core_pdffile")')]
        rows = connection.execute('SELECT * FROM "core_pdffile" ORDER BY id').fetchall()
        inventories = []
        for row in rows:
            stored_path = row["file"] if "file" in columns else None
            declared_path = row["file_path"] if "file_path" in columns else None
            path_value = stored_path or declared_path
            file_path = _safe_storage_path(media_root, path_value)
            exists = bool(file_path and file_path.is_file())
            file_info = _file_record(media_root, file_path) if exists else None
            page_chunks = _chunk_count(row["page_chunks"] if "page_chunks" in columns else None)
            embeddings = _embedding_metadata(row["chunk_embeddings"] if "chunk_embeddings" in columns else None)
            item: dict[str, Any] = {
                "db_id": row["id"],
                "title": row["title"] if "title" in columns else None,
                "path": Path(path_value).as_posix() if path_value else None,
                "exists": exists,
                "size_bytes": file_info["size_bytes"] if file_info else None,
                "sha256": file_info["sha256"] if file_info else None,
                "file_status": (
                    "present" if exists else "missing"
                    if path_value and file_path is not None
                    else "unsafe-path"
                    if path_value
                    else "missing-path"
                ),
                "metadata": {
                    "category": row["category"] if "category" in columns else None,
                    "subject": row["subject"] if "subject" in columns else None,
                    "lifecycle": row["lifecycle"] if "lifecycle" in columns else None,
                    "indexed": bool(row["indexed"]) if "indexed" in columns else None,
                    "has_extracted_text": bool(row["extracted_text"]) if "extracted_text" in columns else False,
                    "has_text_content": bool(row["text_content"]) if "text_content" in columns else False,
                    "page_chunk_count": page_chunks,
                    "embedding_count": embeddings["count"],
                    "embedding_dimensions": embeddings["dimensions"],
                    "embedding_status": embeddings["status"],
                },
            }
            inventories.append(item)
        return inventories, {"available": True, "columns": sorted(columns), "row_count": len(inventories)}
    finally:
        connection.close()


def _metadata_files(data_root: Path, excluded_roots: set[Path]) -> list[dict[str, Any]]:
    records = []
    for path in _iter_files(data_root):
        if any(root == path or root in path.parents for root in excluded_roots):
            continue
        if path.suffix.lower() in _METADATA_SUFFIXES and _METADATA_NAME.search(path.name):
            records.append(_file_record(data_root, path))
    return sorted(records, key=lambda item: item["path"])


def inspect_faiss_file(path: Path) -> dict[str, Any]:
    """Read FAISS dimensions and vector count without changing the index."""
    result: dict[str, Any] = {
        "loadable": False,
        "dimensions": None,
        "vector_count": None,
    }
    try:
        import faiss
    except ImportError:
        result["load_error"] = "faiss-unavailable"
        return result

    try:
        index = faiss.read_index(str(path))
        result.update(
            {
                "loadable": True,
                "dimensions": int(index.d),
                "vector_count": int(index.ntotal),
            }
        )
    except Exception as exc:  # FAISS exposes several native exception types.
        result["load_error"] = f"faiss-read-error: {exc.__class__.__name__}"
    return result


def _faiss_file_record(root: Path, path: Path) -> dict[str, Any]:
    record = _file_record(root, path)
    record["faiss"] = inspect_faiss_file(path)
    return record


def build_manifest(
    data_root: str | os.PathLike[str],
    *,
    database_path: str | os.PathLike[str] | None = None,
    media_root: str | os.PathLike[str] | None = None,
    faiss_root: str | os.PathLike[str] | None = None,
    chroma_root: str | os.PathLike[str] | None = None,
    static_root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Build a deterministic, content-free inventory for one data root."""
    root = Path(data_root).expanduser().resolve()
    database_path = Path(database_path or root / "db.sqlite3").expanduser().resolve()
    media_root = Path(media_root or root / "media").expanduser().resolve()
    faiss_root = Path(faiss_root or root / "faiss_indexes").expanduser().resolve()
    chroma_root = Path(chroma_root or root / "chroma_db").expanduser().resolve()
    static_root = Path(static_root or root / "staticfiles").expanduser().resolve()
    pdf_rows, pdf_schema = _pdf_rows(database_path, media_root)
    media_inventory = _tree_inventory(root, media_root)
    faiss_inventory = _tree_inventory(root, faiss_root)
    chroma_inventory = _tree_inventory(root, chroma_root)
    static_inventory = _tree_inventory(root, static_root)
    pdf_files = [
        record
        for record in media_inventory["files"]
        if record["path"].lower().endswith(".pdf")
    ]
    pdf_files.sort(key=lambda item: item["path"])
    faiss_files = []
    if faiss_inventory["contract"] == "in-contract":
        faiss_files = sorted(
            (
                {
                    **record,
                    "faiss": inspect_faiss_file(root / record["path"]),
                }
                for record in faiss_inventory["files"]
            ),
            key=lambda item: item["path"],
        )
    metadata_files = _metadata_files(root, {media_root, database_path})

    embedding_rows = [item["metadata"] for item in pdf_rows if item["metadata"]["embedding_count"]]
    dimensions = sorted(
        {
            dimension
            for metadata in embedding_rows
            for dimension in metadata["embedding_dimensions"]
        }
    )
    missing_rows = sum(not item["exists"] for item in pdf_rows)
    quarantined_missing_rows = sum(
        not item["exists"]
        and item["metadata"].get("lifecycle") in {"archived", "deprecated"}
        for item in pdf_rows
    )
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "read_only": True,
        "schema": {
            "inventory_schema": INVENTORY_SCHEMA,
            "django_app": "core",
            "pdf_model": "core.PDFFile",
            "pdf_row_schema": {
                "available": pdf_schema["available"],
                "columns": pdf_schema["columns"],
            },
        },
        "database": _sqlite_inventory(database_path, root),
        "pdfs": pdf_rows,
        "pdf_storage": {
            "root": media_inventory["root"],
            "contract": media_inventory["contract"],
            "files": pdf_files,
            "file_count": media_inventory["file_count"],
        },
        "faiss": {
            "root": faiss_inventory["root"],
            "contract": faiss_inventory["contract"],
            "files": faiss_files,
            "file_count": faiss_inventory["file_count"],
        },
        "chroma": chroma_inventory,
        "static": static_inventory,
        "configured_trees": {
            "media": media_inventory,
            "faiss": faiss_inventory,
            "chroma": chroma_inventory,
            "static": static_inventory,
        },
        "embedding_index": {
            "database_rows_with_embeddings": len(embedding_rows),
            "embedding_dimensions": dimensions,
            "metadata_files": metadata_files,
            "metadata_file_count": len(metadata_files),
        },
        "counts": {
            "pdf_rows": len(pdf_rows),
            "pdf_rows_with_existing_files": len(pdf_rows) - missing_rows,
            "pdf_rows_missing_files": missing_rows,
            "pdf_rows_quarantined_missing_files": quarantined_missing_rows,
            "pdf_storage_files": len(pdf_files),
            "faiss_files": len(faiss_files),
            "chroma_files": len(chroma_inventory["files"]),
            "static_files": len(static_inventory["files"]),
            "metadata_files": len(metadata_files),
        },
    }
    return manifest


def _manifest_artifacts(manifest: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    artifacts: dict[tuple[str, str], dict[str, Any]] = {}
    for item in manifest.get("pdfs", []):
        key = str(item.get("path")) if item.get("path") else f"db-id:{item.get('db_id')}"
        artifacts[("pdf", key)] = item
    for item in manifest.get("faiss", {}).get("files", []):
        artifacts[("faiss", str(item.get("path")))] = item
    for item in manifest.get("embedding_index", {}).get("metadata_files", []):
        artifacts[("metadata", str(item.get("path")))] = item
    for tree_name in ("chroma", "static"):
        for item in manifest.get(tree_name, {}).get("files", []):
            artifacts[(tree_name, str(item.get("path")))] = item
    return artifacts


def _artifact_signature(item: dict[str, Any]) -> tuple[Any, Any]:
    return item.get("path"), item.get("sha256")


def _schema_differences(source: dict[str, Any], target: dict[str, Any]) -> list[dict[str, Any]]:
    differences = []

    def compare(left: Any, right: Any, path: str) -> None:
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(set(left) | set(right)):
                compare(left.get(key), right.get(key), f"{path}.{key}" if path else key)
            return
        if left != right:
            differences.append({"field": path, "source": left, "target": right})

    compare(source.get("schema"), target.get("schema"), "schema")
    compare(source.get("database", {}).get("sqlite", {}).get("user_version"),
            target.get("database", {}).get("sqlite", {}).get("user_version"),
            "database.sqlite.user_version")
    compare(source.get("database", {}).get("migrations"),
            target.get("database", {}).get("migrations"),
            "database.migrations")
    return sorted(differences, key=lambda difference: difference["field"])


def _database_hash_differences(source: dict[str, Any], target: dict[str, Any]) -> list[dict[str, Any]]:
    source_database = source.get("database", {})
    target_database = target.get("database", {})
    if source_database.get("sha256") == target_database.get("sha256"):
        return []
    return [{
        "field": "database.sha256",
        "source": source_database.get("sha256"),
        "target": target_database.get("sha256"),
    }]


def compare_manifests(source: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    """Compare two manifests without accessing either data root."""
    source_artifacts = _manifest_artifacts(source)
    target_artifacts = _manifest_artifacts(target)
    exact_matches = []
    conflicts = []
    source_only = []
    target_only = []
    missing_files = []

    for key in sorted(set(source_artifacts) | set(target_artifacts)):
        source_item = source_artifacts.get(key)
        target_item = target_artifacts.get(key)
        label = {"kind": key[0], "key": key[1]}
        if source_item is None:
            target_only.append(label)
            if not target_item.get("exists", True):
                missing_files.append({**label, "source_exists": None, "target_exists": False})
        elif target_item is None:
            source_only.append(label)
            if not source_item.get("exists", True):
                missing_files.append({**label, "source_exists": False, "target_exists": None})
        elif not source_item.get("exists", True) or not target_item.get("exists", True):
            missing_files.append({
                **label,
                "source_exists": source_item.get("exists"),
                "target_exists": target_item.get("exists"),
            })
        elif _artifact_signature(source_item) == _artifact_signature(target_item):
            exact_matches.append(label)
        else:
            conflicts.append({
                **label,
                "source": _artifact_signature(source_item),
                "target": _artifact_signature(target_item),
            })

    differences = _schema_differences(source, target)
    database_hash_differences = _database_hash_differences(source, target)
    return {
        "manifest_version": MANIFEST_VERSION,
        "comparison_schema": INVENTORY_SCHEMA,
        "classifications": {
            "exact_matches": exact_matches,
            "path_hash_conflicts": conflicts,
            "source_only": source_only,
            "target_only": target_only,
            "missing_file": missing_files,
            "schema_migration_differences": differences,
            "database_hash_differences": database_hash_differences,
        },
        "counts": {
            "exact_matches": len(exact_matches),
            "path_hash_conflicts": len(conflicts),
            "source_only": len(source_only),
            "target_only": len(target_only),
            "missing_file": len(missing_files),
            "schema_migration_differences": len(differences),
            "database_hash_differences": len(database_hash_differences),
        },
    }


def _write_json(document: dict[str, Any], output: str | None, stdout: Any) -> None:
    serialized = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    if output:
        Path(output).write_text(serialized, encoding="utf-8")
    else:
        stdout.write(serialized)


class Command(BaseCommand):
    help = "Emit a read-only deterministic inventory of one PdfSearch data root"

    def add_arguments(self, parser):
        parser.add_argument(
            "--data-root",
            default=str(getattr(settings, "DATA_ROOT", Path("data"))),
            help="Data root containing db.sqlite3, media, and faiss_indexes",
        )
        parser.add_argument("--output", help="Write JSON to this file instead of stdout")
        parser.add_argument(
            "--expected-count",
            action="append",
            default=[],
            metavar="NAME=VALUE",
            help="Embed an explicit expected-count policy; repeatable",
        )
        parser.add_argument(
            "--compare",
            nargs=2,
            metavar=("SOURCE_MANIFEST", "TARGET_MANIFEST"),
            help="Compare two existing manifest JSON files without reading their data roots",
        )

    def handle(self, *args, **options):
        if options["compare"]:
            try:
                source = json.loads(Path(options["compare"][0]).read_text(encoding="utf-8"))
                target = json.loads(Path(options["compare"][1]).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise CommandError(f"Unable to read comparison manifest: {exc}") from exc
            if not isinstance(source, dict) or not isinstance(target, dict):
                raise CommandError("Comparison manifests must contain JSON objects")
            _write_json(compare_manifests(source, target), options["output"], self.stdout)
            return

        data_root = Path(options["data_root"]).expanduser().resolve()
        configured_root = Path(getattr(settings, "DATA_ROOT", data_root)).expanduser().resolve()
        configured_paths = {}
        if data_root == configured_root:
            configured_paths = {
                "database_path": settings.DATABASES["default"]["NAME"],
                "media_root": settings.MEDIA_ROOT,
                "faiss_root": settings.FAISS_INDEX_DIR,
                "chroma_root": settings.CHROMA_DIR,
                "static_root": settings.STATIC_ROOT,
            }
        manifest = build_manifest(data_root, **configured_paths)
        if options["expected_count"]:
            expected_counts = {}
            for item in options["expected_count"]:
                name, separator, value = item.partition("=")
                if not separator or not name or not value:
                    raise CommandError("--expected-count must use NAME=VALUE")
                try:
                    expected_counts[name] = int(value)
                except ValueError as exc:
                    raise CommandError("--expected-count values must be integers") from exc
            manifest["expected_counts"] = expected_counts
        _write_json(manifest, options["output"], self.stdout)
