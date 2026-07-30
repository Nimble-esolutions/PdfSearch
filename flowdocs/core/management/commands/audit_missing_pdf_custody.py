"""Read-only, redacted custody audit for missing PDF references."""

from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.custody_audit import (
    CustodyAuditError,
    audit_missing_pdf_custody,
    read_hmac_key_file,
)


class Command(BaseCommand):
    help = "Audit missing PDF custody using redacted, read-only evidence"

    def add_arguments(self, parser):
        parser.add_argument("--hmac-key-file", required=True)
        parser.add_argument(
            "--database",
            default=str(settings.DATABASES["default"]["NAME"]),
        )
        parser.add_argument("--media-root", default=str(settings.MEDIA_ROOT))
        parser.add_argument(
            "--profile-key",
            default=getattr(settings, "VAULT_DEFAULT_PROFILE", "production"),
        )
        parser.add_argument(
            "--skip-vault",
            action="store_true",
            help="Audit only explicitly supplied archives",
        )
        parser.add_argument(
            "--archive",
            action="append",
            default=[],
            help="Explicit tar archive path; repeatable",
        )
        parser.add_argument("--max-generation-pages", type=int, default=100)
        parser.add_argument("--max-archive-members", type=int, default=250_000)
        parser.add_argument(
            "--max-candidate-bytes",
            type=int,
            default=512 * 1024 * 1024,
        )
        parser.add_argument(
            "--max-archive-logical-bytes",
            type=int,
            default=20 * 1024 * 1024 * 1024,
        )
        parser.add_argument(
            "--max-archive-physical-bytes",
            type=int,
            default=20 * 1024 * 1024 * 1024,
        )
        parser.add_argument(
            "--max-archive-read-bytes",
            type=int,
            default=24 * 1024 * 1024 * 1024,
        )
        parser.add_argument(
            "--max-total-archive-physical-bytes",
            type=int,
            default=40 * 1024 * 1024 * 1024,
        )
        parser.add_argument(
            "--max-total-archive-read-bytes",
            type=int,
            default=48 * 1024 * 1024 * 1024,
        )
        parser.add_argument("--max-compression-ratio", type=int, default=100)
        parser.add_argument(
            "--max-seconds",
            type=int,
            default=900,
            help="Cooperative local-work deadline; not an in-flight provider timeout",
        )
        parser.add_argument("--max-generations", type=int, default=10_000)
        parser.add_argument("--max-database-rows", type=int, default=1_000_000)
        parser.add_argument("--max-manifest-entries", type=int, default=1_000_000)
        parser.add_argument("--max-media-probes", type=int, default=1_000_000)
        parser.add_argument(
            "--max-evidence-per-reference",
            type=int,
            default=100,
        )
        parser.add_argument("--max-total-evidence", type=int, default=100_000)

    def handle(self, *args, **options):
        del args
        try:
            key = read_hmac_key_file(Path(options["hmac_key_file"]))
            vault = profile = list_ids = verify = None
            if not options["skip_vault"]:
                from vaultops.models import VaultConnectionProfile
                from vaultops.services.inventory import (
                    list_generation_ids,
                    verify_generation,
                )
                from vaultops.services.profiles import vault_for_profile

                profile = (
                    VaultConnectionProfile.objects.using("control")
                    .filter(key=options["profile_key"])
                    .first()
                )
                if profile is None:
                    raise CustodyAuditError("vault_profile_unavailable")
                vault = vault_for_profile(profile)
                list_ids = list_generation_ids
                verify = verify_generation
            result = audit_missing_pdf_custody(
                database=Path(options["database"]),
                media_root=Path(options["media_root"]),
                hmac_key=key,
                archives=[Path(value) for value in options["archive"]],
                vault=vault,
                profile=profile,
                list_generation_ids=list_ids,
                verify_generation=verify,
                max_pages=options["max_generation_pages"],
                max_archive_members=options["max_archive_members"],
                max_candidate_bytes=options["max_candidate_bytes"],
                max_archive_logical_bytes=options["max_archive_logical_bytes"],
                max_archive_physical_bytes=options[
                    "max_archive_physical_bytes"
                ],
                max_archive_read_bytes=options["max_archive_read_bytes"],
                max_total_archive_physical_bytes=options[
                    "max_total_archive_physical_bytes"
                ],
                max_total_archive_read_bytes=options[
                    "max_total_archive_read_bytes"
                ],
                max_compression_ratio=options["max_compression_ratio"],
                max_seconds=options["max_seconds"],
                max_generations=options["max_generations"],
                max_evidence_per_reference=options[
                    "max_evidence_per_reference"
                ],
                max_total_evidence=options["max_total_evidence"],
                max_database_rows=options["max_database_rows"],
                max_manifest_entries=options["max_manifest_entries"],
                max_media_probes=options["max_media_probes"],
            )
        except CustodyAuditError as exc:
            raise CommandError(str(exc)) from exc
        except Exception as exc:
            raise CommandError("custody_audit_failed") from exc
        self.stdout.write(
            json.dumps(result, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        )
