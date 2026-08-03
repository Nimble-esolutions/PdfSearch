"""Stage and verify one artifact-vault generation without activating it."""

from __future__ import annotations

import json
import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from dataops.config import resolve_profiles
from dataops.executor import RestoreExecutionError, execute_restore
from dataops.storage import StorageConfigurationError, client_for_profile


class Command(BaseCommand):
    help = "Download and verify an artifact-vault generation into quarantine"

    def add_arguments(self, parser):
        parser.add_argument("--profile", required=True, help="Configured Data Operations profile key")
        parser.add_argument("--release-id", required=True, help="Exact generation/release identifier")
        parser.add_argument(
            "--destination",
            default=None,
            help="Quarantine root; defaults to the shared DATA_ROOT recovery workspace",
        )

    def handle(self, *args, **options):
        profile_key = options["profile"].strip().lower()
        profile = next((item for item in resolve_profiles() if item.key == profile_key), None)
        if profile is None or profile.role not in {"restore", "both"}:
            raise CommandError("restore_profile_not_configured")
        destination = options["destination"] or str(
            getattr(
                settings,
                "DATAOPS_RESTORE_STAGING_ROOT",
                Path(getattr(settings, "DATA_ROOT", "/tmp"))
                / "restore-quarantine",
            )
        )
        try:
            client = client_for_profile(profile, os.environ)
            receipt = execute_restore(client, profile, release_id=options["release_id"], destination_root=destination)
        except (StorageConfigurationError, RestoreExecutionError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(json.dumps(receipt, sort_keys=True)))
