"""Publish a legacy data-root snapshot as an immutable vault generation.

This is an internal operator tool, not an application startup path.  It is
deliberately dry-run-first and never advances the authoritative pointer.  A
production volume should be mounted read-only (or copied to a disposable
workspace) before this command is run.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from core.artifact_vault import ArtifactVault, ArtifactVaultError
from core.management.commands.inventory_artifacts import build_manifest
from core.namespace import KeyBuilder

def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_snapshot(database_path: Path, destination: Path) -> None:
    if not database_path.is_file():
        raise CommandError(f"SQLite database not found: {database_path}")

    source = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    snapshot = sqlite3.connect(destination)
    try:
        source.backup(snapshot)
        integrity = snapshot.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise CommandError(f"SQLite integrity check failed: {integrity}")
    except sqlite3.Error as exc:
        raise CommandError(f"Unable to snapshot SQLite database: {exc.__class__.__name__}") from exc
    finally:
        snapshot.close()
        source.close()


def _entry_path(entry: dict[str, Any]) -> str:
    path = entry.get("path")
    if not isinstance(path, str) or not path or Path(path).is_absolute():
        raise CommandError("Inventory contains an unsafe or missing artifact path")
    return Path(path).as_posix()


def _source_file(source_root: Path, path: str) -> Path:
    candidate = (source_root / path).resolve()
    try:
        candidate.relative_to(source_root)
    except ValueError as exc:
        raise CommandError(f"Inventory path escapes source root: {path}") from exc
    return candidate


def _file_entries(
    source_root: Path,
    inventory: dict[str, Any],
    generation_id: str,
    dataset_id: str,
    snapshot_path: Path,
    include_static: bool,
) -> list[dict[str, Any]]:
    keys = KeyBuilder(dataset_id)
    files: list[dict[str, Any]] = []
    seen_paths: set[str] = set()

    def add(path: str, artifact_type: str, object_key: str, local_path: Path) -> None:
        normalized = Path(path).as_posix()
        if normalized in seen_paths:
            return
        if not local_path.is_file():
            raise CommandError(f"Inventory artifact is missing: {normalized}")
        payload = local_path.read_bytes()
        files.append(
            {
                "path": normalized,
                "bytes": len(payload),
                "sha256": _sha256_bytes(payload),
                "object_key": object_key,
                "artifact_type": artifact_type,
            }
        )
        seen_paths.add(normalized)

    add(
        "db.sqlite3",
        "database",
        keys.generation_database(generation_id),
        snapshot_path,
    )

    for item in inventory.get("pdf_storage", {}).get("files", []):
        path = _entry_path(item)
        add(
            path,
            "pdf",
            keys.blob_pdf(str(item.get("sha256", ""))),
            _source_file(source_root, path),
        )

    for item in inventory.get("faiss", {}).get("files", []):
        path = _entry_path(item)
        add(
            path,
            "faiss",
            keys.generation_metadata(generation_id, path),
            _source_file(source_root, path),
        )

    for tree_name in ("chroma", "static"):
        if tree_name == "static" and not include_static:
            continue
        for item in inventory.get(tree_name, {}).get("files", []):
            path = _entry_path(item)
            add(
                path,
                tree_name,
                keys.generation_metadata(generation_id, path),
                _source_file(source_root, path),
            )

    for item in inventory.get("embedding_index", {}).get("metadata_files", []):
        path = _entry_path(item)
        add(
            path,
            "embedding-metadata",
            keys.generation_metadata(generation_id, path),
            _source_file(source_root, path),
        )

    return files


def build_release_manifest(
    source_root: str | Path,
    *,
    database_path: str | Path,
    media_root: str | Path | None,
    faiss_root: str | Path | None,
    chroma_root: str | Path | None,
    static_root: str | Path | None,
    dataset_id: str,
    generation_id: str,
    snapshot_path: str | Path,
    source_label: str,
    include_static: bool = False,
) -> dict[str, Any]:
    """Build a scoped, content-addressed release manifest without S3 calls."""
    root = Path(source_root).expanduser().resolve()
    inventory = build_manifest(
        root,
        database_path=database_path,
        media_root=media_root,
        faiss_root=faiss_root,
        chroma_root=chroma_root,
        static_root=static_root,
    )
    # The consistent snapshot is intentionally outside the source tree. Keep
    # its manifest identity portable instead of leaking the temporary path.
    inventory["database"]["path"] = "db.sqlite3"
    inventory["database"]["contract"] = "in-contract"
    inventory["database"].pop("reason", None)
    files = _file_entries(
        root,
        inventory,
        generation_id,
        dataset_id,
        Path(snapshot_path),
        include_static,
    )
    return {
        "manifest_version": 1,
        "read_only": True,
        "release_id": generation_id,
        "dataset_id": dataset_id,
        "source": {
            "kind": "legacy-data-root",
            "label": source_label,
            "root_contract": "mounted-read-only-snapshot",
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
        "inventory": inventory,
        "database": inventory.get("database", {}),
        "pdf_storage": inventory.get("pdf_storage", {}),
        "faiss": inventory.get("faiss", {}),
        "chroma": inventory.get("chroma", {}),
        "embedding_index": inventory.get("embedding_index", {}),
        "static": {
            **(inventory.get("static", {}) if include_static else {}),
            "included": include_static,
        },
        "files": files,
        "counts": {
            "files": len(files),
            "pdfs": sum(item["artifact_type"] == "pdf" for item in files),
            "faiss": sum(item["artifact_type"] == "faiss" for item in files),
            "embedding_metadata": sum(item["artifact_type"] == "embedding-metadata" for item in files),
            "chroma": sum(item["artifact_type"] == "chroma" for item in files),
            "static": sum(item["artifact_type"] == "static" for item in files),
        },
    }


def _manifest_bytes(manifest: dict[str, Any]) -> bytes:
    return (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _put_immutable(vault: ArtifactVault, key: str, data: bytes, content_type: str) -> str:
    """Create one object without permitting overwrite; deduplicate exact bytes."""
    digest = _sha256_bytes(data)
    try:
        vault.client.put_object(
            Bucket=vault.config.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            Metadata={"sha256": digest, "immutable": "true"},
            IfNoneMatch="*",
        )
        return "uploaded"
    except Exception as exc:
        try:
            existing = vault.head(key)
        except Exception as head_exc:
            raise ArtifactVaultError(
                "Immutable upload failed and existing-object verification was unavailable"
            ) from head_exc
        if existing.sha256 == digest and existing.size == len(data):
            return "already-present"
        raise ArtifactVaultError(f"Immutable object already exists with different content: {key}") from exc


class Command(BaseCommand):
    help = "Dry-run-first internal tool for publishing a legacy data-root snapshot to the vault"

    def add_arguments(self, parser):
        parser.add_argument("--source-root", required=True, type=Path)
        parser.add_argument("--database", type=Path, help="SQLite path; defaults to SOURCE_ROOT/db.sqlite3")
        parser.add_argument("--media-root", type=Path)
        parser.add_argument("--faiss-root", type=Path)
        parser.add_argument("--chroma-root", type=Path)
        parser.add_argument("--static-root", type=Path)
        parser.add_argument("--dataset-id", required=True)
        parser.add_argument("--generation-id", help="Immutable id; generated when omitted")
        parser.add_argument("--source-label", default="legacy-container-volume")
        parser.add_argument("--output", type=Path, help="Write the redacted release manifest to this path")
        parser.add_argument("--include-static", action="store_true")
        parser.add_argument("--publish", action="store_true", help="Upload after local validation; never advances the pointer")
        parser.add_argument("--register-dataset", action="store_true", help="Create the dataset registration after upload")
        parser.add_argument("--production-source-id")
        parser.add_argument("--instance-id")
        parser.add_argument("--org-name", default="")

    def handle(self, *args, **options):
        source_root = options["source_root"].expanduser().resolve()
        if not source_root.is_dir():
            raise CommandError(f"Source root not found: {source_root}")
        if options["register_dataset"] and not options["publish"]:
            raise CommandError("--register-dataset requires --publish")
        if options["register_dataset"] and not options["production_source_id"]:
            raise CommandError("--production-source-id is required with --register-dataset")

        database_path = (options["database"] or source_root / "db.sqlite3").expanduser().resolve()
        generation_id = options["generation_id"] or (
            "legacy-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4)
        )
        snapshot_dir = Path(tempfile.mkdtemp(prefix="pdfsearch-legacy-publish-"))
        snapshot_path = snapshot_dir / "db.sqlite3"
        try:
            _read_snapshot(database_path, snapshot_path)
            manifest = build_release_manifest(
                source_root,
                database_path=snapshot_path,
                media_root=options["media_root"] or source_root / "media",
                faiss_root=options["faiss_root"] or source_root / "faiss_indexes",
                chroma_root=options["chroma_root"] or source_root / "chroma_db",
                static_root=options["static_root"] or source_root / "staticfiles",
                dataset_id=options["dataset_id"],
                generation_id=generation_id,
                snapshot_path=snapshot_path,
                source_label=options["source_label"],
                include_static=options["include_static"],
            )
            data = _manifest_bytes(manifest)
            if options["output"]:
                options["output"].expanduser().write_bytes(data)
            self.stdout.write(json.dumps({"manifest": generation_id, "counts": manifest["counts"]}, sort_keys=True))

            if not options["publish"]:
                self.stdout.write(self.style.WARNING("DRY RUN: no vault objects or control records were changed"))
                return

            vault = ArtifactVault()
            if not vault.enabled:
                raise CommandError("--publish requires ARTIFACT_VAULT_ENABLED=1 and complete vault configuration")
            uploaded = {"uploaded": 0, "already_present": 0}
            for entry in manifest["files"]:
                local_path = (
                    snapshot_path
                    if entry["artifact_type"] == "database"
                    else _source_file(source_root, entry["path"])
                )
                status = _put_immutable(vault, entry["object_key"], local_path.read_bytes(), "application/octet-stream")
                uploaded[status.replace("-", "_")] += 1
            status = _put_immutable(vault, KeyBuilder(options["dataset_id"]).generation_manifest(generation_id), data, "application/json")
            uploaded[status.replace("-", "_")] += 1

            if options["register_dataset"]:
                from core.registration import register_dataset

                register_dataset(
                    vault,
                    options["dataset_id"],
                    production_source_id=options["production_source_id"],
                    instance_id=options["instance_id"] or "",
                    app_identifier="pdfsearch",
                    org_name=options["org_name"],
                )
            self.stdout.write(self.style.SUCCESS(json.dumps({"published": generation_id, **uploaded}, sort_keys=True)))
            self.stdout.write(self.style.WARNING("Authoritative pointer unchanged; promotion remains a separate audited operation"))
        except (ArtifactVaultError, OSError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        finally:
            for path in snapshot_dir.glob("*"):
                path.unlink(missing_ok=True)
            snapshot_dir.rmdir()
