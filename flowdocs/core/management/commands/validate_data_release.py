from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core.data_release_validation import DataReleaseValidationError, validate_release


class Command(BaseCommand):
    help = "Explicitly validate an inventory manifest against a data root without mutating it"

    def add_arguments(self, parser):
        parser.add_argument(
            "--manifest",
            required=True,
            type=Path,
            help="Path to an inventory JSON manifest",
        )
        parser.add_argument(
            "--data-root",
            required=True,
            type=Path,
            help="Data root containing db.sqlite3, media, and faiss_indexes",
        )
        parser.add_argument(
            "--expected-count",
            action="append",
            default=[],
            metavar="NAME=VALUE",
            help="Override an expected-count policy entry; repeatable",
        )
        parser.add_argument("--output", type=Path, help="Write the validation report to this file")

    def handle(self, *args, **options):
        manifest_path: Path = options["manifest"]
        if not manifest_path.is_file():
            raise CommandError(f"Manifest file not found: {manifest_path}")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            overrides = self._parse_expected_counts(options["expected_count"])
            report = validate_release(manifest, options["data_root"], expected_counts=overrides)
        except (OSError, json.JSONDecodeError, DataReleaseValidationError) as exc:
            raise CommandError(str(exc)) from exc

        serialized = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
        if options["output"]:
            options["output"].write_text(serialized, encoding="utf-8")
        else:
            self.stdout.write(serialized, ending="")
        if not report["ok"]:
            raise CommandError(f"Data release validation failed with {len(report['issues'])} issue(s)")
        self.stdout.write(self.style.SUCCESS("Data release validation passed"))

    @staticmethod
    def _parse_expected_counts(values: list[str]) -> dict[str, int]:
        result = {}
        for item in values:
            name, separator, value = item.partition("=")
            if not separator or not name or not value:
                raise DataReleaseValidationError("--expected-count must use NAME=VALUE")
            try:
                result[name] = int(value)
            except ValueError as exc:
                raise DataReleaseValidationError("--expected-count values must be integers") from exc
        return result
