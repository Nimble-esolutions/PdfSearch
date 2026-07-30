"""Read-only, redacted custody audit for missing PDF references."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.custody_audit import CustodyAuditError, audit_missing_pdf_custody


def _read_hmac_key(path_value: str) -> bytes:
    path = Path(path_value)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise CustodyAuditError("hmac_key_file_unavailable") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
            or metadata.st_size < 32
            or metadata.st_size > 1024
        ):
            raise CustodyAuditError("hmac_key_file_unsafe")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            key = stream.read(1025)
    except OSError as exc:
        raise CustodyAuditError("hmac_key_file_unavailable") from exc
    finally:
        os.close(descriptor)
    if len(key) < 32:
        raise CustodyAuditError("hmac_key_too_short")
    return key


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

    def handle(self, *args, **options):
        del args
        try:
            key = _read_hmac_key(options["hmac_key_file"])
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
            )
        except CustodyAuditError as exc:
            raise CommandError(str(exc)) from exc
        except Exception as exc:
            raise CommandError("custody_audit_failed") from exc
        self.stdout.write(
            json.dumps(result, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        )
