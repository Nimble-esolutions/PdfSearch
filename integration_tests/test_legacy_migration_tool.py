"""Real S3-compatible publication/promotion flow for the operator-side tool.

Run in the disposable MinIO/RustFS integration environment; never point these
tests at an operator or production bucket.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import secrets
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import boto3


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPOSITORY_ROOT / "scripts" / "ops" / "migrate_legacy_volume_to_vault.py"
SPEC = importlib.util.spec_from_file_location("legacy_migration_tool", MODULE_PATH)
migration = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(migration)

ENDPOINT = os.environ.get("ARTIFACT_VAULT_ENDPOINT", "http://pdfsearch-minio:9000")
REGION = os.environ.get("ARTIFACT_VAULT_REGION", "us-east-1")
ACCESS_KEY = os.environ.get("ARTIFACT_VAULT_ACCESS_KEY", "minioadmin")
SECRET_KEY = os.environ.get("ARTIFACT_VAULT_SECRET_KEY", "minioadmin")
BUCKET = os.environ.get("LEGACY_MIGRATION_TEST_BUCKET", "pdfsearch-test")


def client():
    return boto3.client(
        "s3",
        endpoint_url=ENDPOINT,
        region_name=REGION,
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
    )


class LegacyMigrationS3IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.client = client()
        try:
            self.client.create_bucket(Bucket=BUCKET)
        except Exception as exc:
            code = str(
                getattr(exc, "response", {}).get("Error", {}).get("Code", "")
            )
            if code not in {"BucketAlreadyExists", "BucketAlreadyOwnedByYou"}:
                raise
        self.dataset_id = f"legacy-tool-test-{secrets.token_hex(5)}"
        self.prefix = f"datasets/{self.dataset_id}/"

    def tearDown(self):
        continuation = None
        while True:
            params = {"Bucket": BUCKET, "Prefix": self.prefix}
            if continuation:
                params["ContinuationToken"] = continuation
            response = self.client.list_objects_v2(**params)
            for item in response.get("Contents", []):
                self.client.delete_object(Bucket=BUCKET, Key=item["Key"])
            if not response.get("IsTruncated"):
                break
            continuation = response["NextContinuationToken"]

    def args(
        self,
        *,
        source_root=None,
        checkpoint=None,
        publish=False,
        promote_generation=None,
        register_dataset=False,
    ):
        generation = "legacy-20260726T120000Z-integration"
        return argparse.Namespace(
            source_root=source_root,
            database=None,
            dataset_id=self.dataset_id,
            bucket=BUCKET,
            production_source_id=self.dataset_id,
            generation_id=generation,
            source_label="disposable-integration-source",
            output=None,
            checkpoint=checkpoint,
            include_backups=False,
            include_static=False,
            publish=publish,
            candidate_only=False,
            register_dataset=register_dataset,
            promote_generation=promote_generation,
            confirm_promotion=(
                f"{self.dataset_id}:{promote_generation}"
                if promote_generation
                else ""
            ),
            writer_ttl_seconds=120,
        )

    def test_candidate_retry_then_separate_fenced_promotion(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_root = Path(temp_dir) / "source"
            source_root.mkdir()
            (source_root / "media").mkdir()
            (source_root / "media" / "sample.pdf").write_bytes(b"%PDF-test")
            database = sqlite3.connect(source_root / "db.sqlite3")
            database.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY)")
            database.commit()
            database.close()
            checkpoint = Path(temp_dir) / "checkpoint.json"
            publish_args = self.args(
                source_root=source_root,
                checkpoint=checkpoint,
                publish=True,
                register_dataset=True,
            )

            with mock.patch.object(
                migration, "s3_client", return_value=self.client
            ):
                first = migration.migrate(publish_args)
                retry = migration.migrate(publish_args)

            self.assertTrue(first["candidate_published"])
            self.assertFalse(first["pointer_updated"])
            self.assertEqual(first["manifest_sha256"], retry["manifest_sha256"])
            self.assertGreater(retry["already_present"], 0)

            generation = publish_args.generation_id
            promote_args = self.args(promote_generation=generation)
            with mock.patch.object(
                migration, "s3_client", return_value=self.client
            ):
                promoted = migration.migrate(promote_args)

            self.assertTrue(promoted["pointer_updated"])
            pointer = json.loads(
                self.client.get_object(
                    Bucket=BUCKET,
                    Key=migration.pointer_key(self.dataset_id),
                )["Body"].read()
            )
            self.assertEqual(pointer["generation_id"], generation)
            self.assertGreaterEqual(pointer["writer_epoch"], 1)


if __name__ == "__main__":
    unittest.main()
