"""Automatic connection capability evidence for DataOps v3."""

from __future__ import annotations

import hashlib
import io
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from dataops.models import DataConnection
from dataops.v3_connection import (
    V3ConnectionError,
    connection_is_ready,
    ensure_connection_readable,
    probe_owned_connection,
)


class FakeS3:
    def __init__(
        self,
        *,
        conditional=True,
        accessible=True,
        metadata=True,
        bulk_delete=True,
    ):
        self.conditional = conditional
        self.accessible = accessible
        self.metadata = metadata
        self.bulk_delete = bulk_delete
        self.objects = {}

    @staticmethod
    def _etag(body):
        return hashlib.md5(body, usedforsecurity=False).hexdigest()

    def head_bucket(self, *, Bucket):
        if not self.accessible:
            raise RuntimeError("forbidden detail must not be persisted")
        return {}

    def put_object(self, *, Bucket, Key, Body, Metadata=None, **kwargs):
        body = Body.read() if hasattr(Body, "read") else bytes(Body)
        current = self.objects.get((Bucket, Key))
        if self.conditional:
            if current and kwargs.get("IfNoneMatch") == "*":
                raise RuntimeError("precondition")
            if "IfMatch" in kwargs and (
                current is None or current["etag"] != kwargs["IfMatch"]
            ):
                raise RuntimeError("precondition")
        item = {
            "body": body,
            "etag": self._etag(body),
            "metadata": dict(Metadata or {}),
        }
        self.objects[(Bucket, Key)] = item
        return {"ETag": item["etag"]}

    def get_object(self, *, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)]["body"])}

    def head_object(self, *, Bucket, Key):
        item = self.objects[(Bucket, Key)]
        return {
            "ETag": item["etag"],
            "Metadata": dict(item["metadata"]) if self.metadata else {},
        }

    def get_bucket_versioning(self, *, Bucket):
        return {"Status": "Enabled"}

    def list_objects_v2(self, *, Bucket, Prefix):
        return {
            "Contents": [
                {"Key": key}
                for bucket, key in self.objects
                if bucket == Bucket and key.startswith(Prefix)
            ]
        }

    def delete_objects(self, *, Bucket, Delete):
        if not self.bulk_delete:
            raise RuntimeError("bulk delete unsupported")
        for item in Delete["Objects"]:
            self.objects.pop((Bucket, item["Key"]), None)
        return {}

    def delete_object(self, *, Bucket, Key):
        self.objects.pop((Bucket, Key), None)
        return {}


class V3ConnectionTests(TestCase):
    databases = {"default", "control"}

    def setUp(self):
        self.connection = DataConnection.objects.using("control").create(
            name="Owned recovery storage",
            provider="rustfs",
            endpoint="https://rustfs.example.invalid",
            bucket="stage-recovery",
            dataset_id="ai-sahakar-stage-2026",
            credential_ref="secret://dataops/stage",
            is_primary=True,
        )

    def test_successful_probe_records_actual_capabilities_and_cleans_up(self):
        client = FakeS3()
        probe_owned_connection(
            self.connection,
            deployment_id="stage-2026",
            client_factory=lambda _connection: client,
        )
        self.connection.refresh_from_db(using="control")
        self.assertTrue(connection_is_ready(self.connection))
        self.assertEqual(self.connection.observation["status"], "ready")
        self.assertEqual(self.connection.observation["failure_codes"], [])
        self.assertEqual(client.objects, {})

    def test_missing_conditional_semantics_blocks_publication(self):
        with self.assertRaisesRegex(V3ConnectionError, "conditional_writes_unsupported"):
            probe_owned_connection(
                self.connection,
                client_factory=lambda _connection: FakeS3(conditional=False),
            )
        self.connection.refresh_from_db(using="control")
        self.assertFalse(self.connection.capabilities["conditional_write"])
        self.assertEqual(self.connection.observation["status"], "blocked")

    def test_missing_object_metadata_uses_digest_readback_without_blocking(self):
        probe_owned_connection(
            self.connection,
            client_factory=lambda _connection: FakeS3(metadata=False),
        )
        self.connection.refresh_from_db(using="control")
        self.assertTrue(connection_is_ready(self.connection))
        self.assertTrue(self.connection.capabilities["write"])
        self.assertFalse(self.connection.capabilities["metadata"])

    def test_probe_cleanup_falls_back_when_bulk_delete_is_unsupported(self):
        client = FakeS3(bulk_delete=False)
        probe_owned_connection(
            self.connection,
            client_factory=lambda _connection: client,
        )
        self.assertEqual(client.objects, {})

    def test_provider_exception_details_are_not_persisted(self):
        with self.assertRaises(V3ConnectionError):
            probe_owned_connection(
                self.connection,
                client_factory=lambda _connection: FakeS3(accessible=False),
            )
        self.connection.refresh_from_db(using="control")
        serialized = str(self.connection.observation)
        self.assertIn("bucket_access_failed", serialized)
        self.assertNotIn("forbidden detail", serialized)

    def test_stale_evidence_is_refreshed_by_policy_not_an_environment_flag(self):
        self.connection.capabilities = {
            "probed": True,
            "read": True,
            "write": True,
            "conditional_write": True,
        }
        self.connection.last_probed_at = timezone.now() - timedelta(hours=2)
        self.connection.save(
            using="control",
            update_fields=["capabilities", "last_probed_at", "updated_at"],
        )
        self.assertFalse(connection_is_ready(self.connection))

    def test_read_only_check_does_not_claim_backup_capabilities(self):
        client = FakeS3()
        ensure_connection_readable(
            self.connection,
            client_factory=lambda _connection: client,
        )
        self.connection.refresh_from_db(using="control")
        self.assertTrue(self.connection.capabilities["read"])
        self.assertTrue(self.connection.capabilities["read_probed"])
        self.assertFalse(self.connection.capabilities["probed"])
        self.assertFalse(connection_is_ready(self.connection))

    def test_failed_read_check_persists_only_typed_failure(self):
        with self.assertRaisesRegex(
            V3ConnectionError,
            "source_connection_not_readable",
        ):
            ensure_connection_readable(
                self.connection,
                client_factory=lambda _connection: FakeS3(accessible=False),
            )
        self.connection.refresh_from_db(using="control")
        self.assertEqual(
            self.connection.observation["failure_codes"],
            ["source_connection_not_readable"],
        )
        self.assertNotIn("forbidden detail", str(self.connection.observation))
