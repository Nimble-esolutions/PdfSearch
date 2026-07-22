from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core.artifact_vault import (
    ArtifactVault,
    ArtifactVaultError,
    object_key_for_manifest_entry,
)


class Command(BaseCommand):
    help = "Explicitly upload an existing inventory manifest and selected immutable artifacts"

    def add_arguments(self, parser):
        parser.add_argument(
            "--manifest",
            required=True,
            type=Path,
            help="Path to an already-generated JSON inventory manifest",
        )
        parser.add_argument(
            "--artifact",
            action="append",
            default=[],
            metavar="MANIFEST_PATH=LOCAL_PATH",
            help="Select an artifact from the manifest and provide its local path; repeatable",
        )
        parser.add_argument(
            "--release-id",
            help="Explicit immutable release/generation id required when the manifest has none",
        )

    def handle(self, *args, **options):
        manifest_path: Path = options["manifest"]
        if not manifest_path.is_file():
            raise CommandError(f"Manifest file not found: {manifest_path}")

        try:
            manifest_bytes = manifest_path.read_bytes()
            vault = ArtifactVault()
            manifest = vault.normalize_manifest(manifest_bytes, release_id=options.get("release_id"))
            entries = {entry["path"]: entry for entry in manifest["files"]}
            selected = [self._parse_artifact(spec) for spec in options["artifact"]]

            uploaded = 0
            for manifest_path_name, local_path in selected:
                entry = entries.get(manifest_path_name)
                if entry is None:
                    raise CommandError(f"Artifact is not declared by the manifest: {manifest_path_name}")
                local_bytes = local_path.read_bytes()
                expected_size = entry["bytes"]
                if len(local_bytes) != expected_size:
                    raise CommandError(f"Artifact byte count does not match manifest: {manifest_path_name}")
                key = object_key_for_manifest_entry(entry)
                metadata = vault.put(key, local_bytes, expected_sha256=entry["sha256"])
                self.stdout.write(f"Uploaded {metadata.key} ({metadata.size} bytes)")
                uploaded += 1

            manifest_metadata = vault.put_manifest(manifest)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Uploaded manifest {manifest_metadata.key}; {uploaded} artifact(s) selected"
                )
            )
        except ArtifactVaultError as exc:
            raise CommandError(str(exc)) from exc
        except OSError as exc:
            raise CommandError(f"Unable to read selected artifact: {exc}") from exc

    @staticmethod
    def _parse_artifact(spec: str) -> tuple[str, Path]:
        manifest_path, separator, local_path = spec.partition("=")
        if not separator or not manifest_path or not local_path:
            raise CommandError("--artifact must use MANIFEST_PATH=LOCAL_PATH")
        path = Path(local_path)
        if not path.is_file():
            raise CommandError(f"Artifact file not found: {path}")
        return manifest_path, path
