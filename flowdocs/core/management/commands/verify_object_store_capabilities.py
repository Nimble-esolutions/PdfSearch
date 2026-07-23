"""Management command: safely certify S3/RustFS object-store capabilities.

Runs isolated probe objects against the configured vault endpoint.
Never touches production control objects, manifests, or blobs.

Usage:
  python manage.py verify_object_store_capabilities [--cleanup] [--format json|text]

Safety: Rejects probe prefixes that overlap with dataset control paths.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from datetime import datetime, timezone

from django.core.management.base import BaseCommand, CommandError

from core.object_store_capabilities import probe_capabilities, PROBE_PREFIX
from core.artifact_vault import ArtifactVault


UNSAFE_PREFIX_PARTS = {
    "datasets", "control", "registration.json", "writer.json",
    "authoritative.json", "generations", "blobs", "manifests",
}


class Command(BaseCommand):
    help = "Certify S3/RustFS object-store capabilities using safe probe objects"

    def add_arguments(self, parser):
        parser.add_argument("--cleanup", action="store_true",
                          help="Remove probe objects after testing")
        parser.add_argument("--format", choices=["json", "text"], default="text",
                          help="Output format")
        parser.add_argument("--deployment-id", default="",
                          help="Deployment identity for probe namespace")

    def handle(self, *args, **options):
        vault = ArtifactVault()
        if not vault.enabled:
            self.stdout.write(self.style.ERROR(
                "RustFS/object-store vault is not configured (ARTIFACT_VAULT_ENABLED=0)"
            ))
            result = {
                "provider": "unknown",
                "mandatory_capabilities_passed": False,
                "authoritative_publication_safe": False,
                "error": "Vault is disabled or not configured",
            }
            if options["format"] == "json":
                self.stdout.write(json.dumps(result, indent=2))
            sys.exit(1)

        deploy_id = options["deployment_id"] or os.environ.get(
            "DEPLOYMENT_ID", secrets.token_hex(4)
        )
        probe_prefix = f"{PROBE_PREFIX}/{deploy_id}/{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(4)}"

        for unsafe in UNSAFE_PREFIX_PARTS:
            if unsafe in probe_prefix.split("/"):
                raise CommandError(
                    f"Probe prefix contains unsafe component: {unsafe}"
                )

        self.stdout.write(f"Probe prefix: {probe_prefix}")
        self.stdout.write("Running capability probe...")

        cap = probe_capabilities(vault, deployment_id=deploy_id)

        if options["cleanup"]:
            self.stdout.write("Cleaning up probe objects...")
            import boto3
            c = boto3.client(
                "s3",
                endpoint_url=vault.config.endpoint,
                region_name=vault.config.region,
                aws_access_key_id=vault.config.access_key,
                aws_secret_access_key=vault.config.secret_key,
            )
            try:
                resp = c.list_objects_v2(
                    Bucket=vault.config.bucket, Prefix=f"{PROBE_PREFIX}/{deploy_id}/"
                )
                objects = [{"Key": o["Key"]} for o in resp.get("Contents", [])]
                if objects:
                    c.delete_objects(
                        Bucket=vault.config.bucket,
                        Delete={"Objects": objects, "Quiet": True},
                    )
                    self.stdout.write(f"Removed {len(objects)} probe object(s)")
                    cap.errors.append("cleanup succeeded")
            except Exception as exc:
                cap.errors.append(f"cleanup failed: {exc}")
                self.stderr.write(self.style.ERROR(f"Cleanup failed: {exc}"))

        if options["format"] == "json":
            self.stdout.write(json.dumps(cap.summary(), indent=2, default=str))
        else:
            self._text_report(cap)

        if not cap.authoritative_publication_allowed:
            self.stdout.write(self.style.ERROR(
                "\nAuthoritative publication is NOT SAFE on this provider."
            ))
            sys.exit(1)
        else:
            self.stdout.write(self.style.SUCCESS(
                "\nAuthoritative publication is SAFE on this provider."
            ))

    def _text_report(self, cap):
        self.stdout.write(self.style.SUCCESS(
            f"\nConditional create:  {cap.conditional_create_supported}"
        ))
        self.stdout.write(
            f"Conditional replace: {cap.conditional_replace_supported}"
        )
        self.stdout.write(f"ETag available:      {cap.etag_available}")
        self.stdout.write(
            f"Read-after-write:    {cap.read_after_write_consistent}"
        )
        if cap.errors:
            self.stdout.write(self.style.WARNING("\nErrors:"))
            for err in cap.errors:
                self.stdout.write(f"  - {err}")
