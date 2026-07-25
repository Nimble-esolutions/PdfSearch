from __future__ import annotations

import hashlib
import io
import json
import time
import unittest
from types import SimpleNamespace

from .artifact_vault import ArtifactVaultError
from .global_writer import release_global_writer
from .registration import (
    RegistrationError,
    update_authoritative_pointer,
    validate_registration,
)


class FakeClient:
    def __init__(self):
        self.objects = {}
        self.version = 0

    def put_object(self, **params):
        key = params["Key"]
        existing = self.objects.get(key)
        if params.get("IfNoneMatch") == "*" and existing is not None:
            raise RuntimeError("precondition failed")
        if "IfMatch" in params and (
            existing is None or existing["etag"] != params["IfMatch"]
        ):
            raise RuntimeError("precondition failed")
        body = params["Body"]
        data = body.read() if hasattr(body, "read") else bytes(body)
        self.version += 1
        etag = f'"etag-{self.version}"'
        self.objects[key] = {"body": data, "etag": etag}
        return {"ETag": etag}

    def get_object(self, **params):
        value = self.objects[params["Key"]]
        return {"Body": io.BytesIO(value["body"]), "ETag": value["etag"]}


class FakeVault:
    def __init__(self):
        self.client = FakeClient()
        self.config = SimpleNamespace(bucket="test-bucket")

    def get(self, key):
        try:
            return self.client.objects[key]["body"]
        except KeyError as exc:
            raise ArtifactVaultError("missing") from exc


def registration_record():
    return {
        "dataset_id": "dataset-a",
        "registration_version": 1,
        "manifest_schema_range": {"min": 1, "max": 1},
        "app_identifier": "pdfsearch",
        "production_source_id": "source-a",
    }


class RegistrationValidationTests(unittest.TestCase):
    def setUp(self):
        self.vault = FakeVault()
        self.key = "datasets/dataset-a/control/registration.json"

    def store(self, value):
        self.vault.client.put_object(
            Bucket="test-bucket",
            Key=self.key,
            Body=json.dumps(value).encode(),
        )

    def test_registration_requires_exact_app_source_and_schema_range(self):
        self.store(registration_record())
        validated = validate_registration(
            self.vault,
            "dataset-a",
            app_identifier="pdfsearch",
            production_source_id="source-a",
        )
        self.assertEqual(validated["dataset_id"], "dataset-a")

        for missing_field in (
            "app_identifier",
            "production_source_id",
            "manifest_schema_range",
        ):
            value = registration_record()
            value.pop(missing_field)
            self.store(value)
            with self.assertRaises(RegistrationError, msg=missing_field):
                validate_registration(
                    self.vault,
                    "dataset-a",
                    app_identifier="pdfsearch",
                    production_source_id="source-a",
                )

    def test_new_pointer_binds_dataset_identity(self):
        pointer = update_authoritative_pointer(
            self.vault,
            "dataset-a",
            generation_id="generation-1",
            manifest_object_key=(
                "datasets/dataset-a/generations/generation-1/manifest.json"
            ),
            manifest_sha256="a" * 64,
            writer_epoch=1,
            production_source_id="source-a",
        )
        self.assertEqual(pointer["dataset_id"], "dataset-a")


class WriterReleaseTests(unittest.TestCase):
    def setUp(self):
        self.vault = FakeVault()
        self.key = "datasets/dataset-a/control/writer.json"

    def writer(self, token, epoch=1):
        return {
            "schema_version": 1,
            "dataset_id": "dataset-a",
            "production_source_id": "source-a",
            "instance_id": "instance-a",
            "owner_token_hash": hashlib.sha256(token.encode()).hexdigest(),
            "writer_epoch": epoch,
            "heartbeat_at": time.time(),
            "expires_at": time.time() + 120,
        }

    def store(self, value):
        return self.vault.client.put_object(
            Bucket="test-bucket",
            Key=self.key,
            Body=json.dumps(value).encode(),
        )

    def test_release_expires_owned_record_without_deleting_it(self):
        token = "owned-token"
        record = self.writer(token)
        self.store(record)
        caller = {**record, "_token": token}

        release_global_writer(self.vault, "dataset-a", writer_record=caller)

        stored = json.loads(self.vault.client.objects[self.key]["body"])
        self.assertLessEqual(stored["expires_at"], time.time())
        self.assertIn("released_at", stored)

    def test_stale_release_cannot_remove_or_expire_successor(self):
        old_token = "old-token"
        old = self.writer(old_token)
        self.store(old)
        caller = {**old, "_token": old_token}

        successor = self.writer("successor-token", epoch=2)
        successor["instance_id"] = "instance-b"
        self.store(successor)
        release_global_writer(self.vault, "dataset-a", writer_record=caller)

        stored = json.loads(self.vault.client.objects[self.key]["body"])
        self.assertEqual(stored["writer_epoch"], 2)
        self.assertEqual(stored["instance_id"], "instance-b")
        self.assertGreater(stored["expires_at"], time.time())


if __name__ == "__main__":
    unittest.main()
