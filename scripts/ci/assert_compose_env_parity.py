#!/usr/bin/env python3
"""Reject security-critical web/maintenance environment drift."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys


CRITICAL_KEYS = {
    "APP_ENV",
    "DEPLOYMENT_ID",
    "DATASET_ID",
    "AUTHORITATIVE_DATASET_ID",
    "PRODUCTION_SOURCE_ID",
    "DATA_MODE",
    "RESTORE_SOURCE_DATASET_ID",
    "RESTORE_POLICY",
    "BACKUP_ROLE",
    "VAULT_SYNC_ENABLED",
    "VAULT_DEFAULT_PROFILE",
    "VAULT_ALLOWED_S3_ENDPOINTS",
    "VAULT_CREDENTIAL_ALIASES",
    "VAULT_RESTORE_REQUIRE_SANITIZATION",
    "VAULT_UI_PROFILE_CONFIGURATION_ENABLED",
    "VAULT_UI_SECRET_ENTRY_ENABLED",
    "STAGING_RUNTIME_ACTIVATION_ENABLED",
    "STAGING_ACTIVATION_APPLY_MODE",
    "ACTIVATION_INTENT_SIGNING_KEY",
    "ACTIVATION_SMOKE_QUERIES_FILE",
    "ACTIVATION_RECOVERY_SUPERADMIN_USERNAME",
    "ACTIVATION_RECOVERY_SUPERADMIN_PASSWORD",
    "EXTERNAL_SIDE_EFFECTS_MODE",
    "APP_IMAGE_DIGEST",
    "APP_RELEASE_VERSION",
    "NONPROD_DATA_POLICY",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compose-file", default="docker-compose.yml")
    args = parser.parse_args()
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            args.compose_file,
            "config",
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    services = json.loads(result.stdout)["services"]
    web = services["web"].get("environment", {})
    maintenance = services["maintenance"].get("environment", {})
    missing = sorted(
        key for key in CRITICAL_KEYS if key not in web or key not in maintenance
    )
    divergent = sorted(
        key
        for key in CRITICAL_KEYS
        if key in web and key in maintenance and web[key] != maintenance[key]
    )
    if missing or divergent:
        if missing:
            print("missing security-critical keys: " + ", ".join(missing))
        if divergent:
            print("divergent security-critical keys: " + ", ".join(divergent))
        return 1
    print(f"web/maintenance environment parity verified ({len(CRITICAL_KEYS)} keys)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
