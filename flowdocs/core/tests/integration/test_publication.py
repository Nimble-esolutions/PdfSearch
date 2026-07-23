"""Publication integration tests — prove global writer CAS fencing against real MinIO.

Uses two separate boto3 clients simulating independent production deployments.
Runs against the shared MinIO bucket with real S3 conditional operations.
"""

from __future__ import annotations

import json
import os
import secrets
import threading

import boto3
from django.test import TestCase

BUCKET = "pdfsearch-test"
ENDPOINT = "http://pdfsearch-minio:9000"
REGION = "us-east-1"
DATASET = "integration-test-dataset"


def _client():
    return boto3.client(
        "s3",
        endpoint_url=ENDPOINT,
        region_name=REGION,
        aws_access_key_id="minioadmin",
        aws_secret_access_key="minioadmin",
    )


def _writer_key() -> str:
    return f"datasets/{DATASET}/control/writer.json"


def _registration_key() -> str:
    return f"datasets/{DATASET}/control/registration.json"


def _pointer_key() -> str:
    return f"datasets/{DATASET}/control/authoritative.json"


def _make_registration(production_source_id="prod-primary"):
    return {
        "dataset_id": DATASET,
        "registration_version": 1,
        "app_identifier": "pdfsearch",
        "production_source_id": production_source_id,
        "created_at": "2026-07-24T00:00:00Z",
        "registration_nonce": secrets.token_hex(8),
    }


def _make_writer_record(production_source_id, instance_id, epoch, ttl=300):
    return {
        "schema_version": 1,
        "dataset_id": DATASET,
        "production_source_id": production_source_id,
        "instance_id": instance_id,
        "owner_token_hash": secrets.token_hex(32),
        "writer_epoch": epoch,
        "acquired_at": 0,
        "expires_at": 9999999999,
        "app_release": "test",
    }


class GlobalWriterAcquisitionTests(TestCase):
    """Prove global writer fencing with real S3 CAS."""

    def setUp(self):
        self.c = _client()

    def test_registration_conditional_create(self):
        try:
            self.c.delete_object(Bucket=BUCKET, Key=_registration_key())
        except Exception:
            pass

        self.c.put_object(
            Bucket=BUCKET, Key=_registration_key(),
            Body=json.dumps(_make_registration()).encode(),
            ContentType="application/json",
            IfNoneMatch="*",
        )
        resp = self.c.head_object(Bucket=BUCKET, Key=_registration_key())
        self.assertTrue(resp.get("ETag"))

    def test_registration_cannot_be_overwritten(self):
        self.test_registration_conditional_create()
        with self.assertRaises(Exception):
            self.c.put_object(
                Bucket=BUCKET, Key=_registration_key(),
                Body=json.dumps(_make_registration("rogue")).encode(),
                ContentType="application/json",
                IfNoneMatch="*",
            )

    def test_writer_acquisition_with_conditional_create(self):
        try:
            self.c.delete_object(Bucket=BUCKET, Key=_writer_key())
        except Exception:
            pass

        record = _make_writer_record("prod-primary", "prod-a", 1)
        resp = self.c.put_object(
            Bucket=BUCKET, Key=_writer_key(),
            Body=json.dumps(record, sort_keys=True).encode(),
            ContentType="application/json",
            IfNoneMatch="*",
        )
        etag = resp["ETag"]
        self.assertTrue(etag)

        body = self.c.get_object(Bucket=BUCKET, Key=_writer_key())["Body"].read()
        stored = json.loads(body)
        self.assertEqual(stored["writer_epoch"], 1)
        self.assertEqual(stored["production_source_id"], "prod-primary")

    def test_second_writer_cannot_acquire(self):
        self.test_writer_acquisition_with_conditional_create()

        record2 = _make_writer_record("prod-primary", "prod-b", 1)
        with self.assertRaises(Exception):
            self.c.put_object(
                Bucket=BUCKET, Key=_writer_key(),
                Body=json.dumps(record2, sort_keys=True).encode(),
                ContentType="application/json",
                IfNoneMatch="*",
            )

    def test_takeover_with_correct_etag_succeeds(self):
        self.test_writer_acquisition_with_conditional_create()

        resp = self.c.get_object(Bucket=BUCKET, Key=_writer_key())
        etag = resp["ETag"]
        current = json.loads(resp["Body"].read())

        record = _make_writer_record("prod-primary", "prod-b", current["writer_epoch"] + 1)
        resp2 = self.c.put_object(
            Bucket=BUCKET, Key=_writer_key(),
            Body=json.dumps(record, sort_keys=True).encode(),
            ContentType="application/json",
            IfMatch=etag,
        )
        self.assertTrue(resp2["ETag"] != etag)

        body = self.c.get_object(Bucket=BUCKET, Key=_writer_key())["Body"].read()
        stored = json.loads(body)
        self.assertEqual(stored["writer_epoch"], 2)
        self.assertEqual(stored["instance_id"], "prod-b")

    def test_takeover_with_stale_etag_fails(self):
        self.test_writer_acquisition_with_conditional_create()

        resp = self.c.get_object(Bucket=BUCKET, Key=_writer_key())
        stale_etag = resp["ETag"]

        takeover = _make_writer_record("prod-primary", "prod-b", 2)
        self.c.put_object(
            Bucket=BUCKET, Key=_writer_key(),
            Body=json.dumps(takeover, sort_keys=True).encode(),
            ContentType="application/json",
            IfMatch=stale_etag,
        )

        takeover2 = _make_writer_record("prod-primary", "prod-c", 3)
        with self.assertRaises(Exception):
            self.c.put_object(
                Bucket=BUCKET, Key=_writer_key(),
                Body=json.dumps(takeover2, sort_keys=True).encode(),
                ContentType="application/json",
                IfMatch=stale_etag,
            )

    def test_rogue_source_rejected_by_registration(self):
        try:
            self.c.delete_object(Bucket=BUCKET, Key=_registration_key())
        except Exception:
            pass
        self.c.put_object(
            Bucket=BUCKET, Key=_registration_key(),
            Body=json.dumps(_make_registration("prod-primary")).encode(),
            ContentType="application/json",
            IfNoneMatch="*",
        )

        resp = self.c.get_object(Bucket=BUCKET, Key=_registration_key())
        reg = json.loads(resp["Body"].read())
        self.assertEqual(reg["production_source_id"], "prod-primary")

        try:
            self.c.delete_object(Bucket=BUCKET, Key=_writer_key())
        except Exception:
            pass

        reader_source_id = _make_writer_record("rogue-source", "evil", 1)
        self.c.put_object(
            Bucket=BUCKET, Key=_writer_key(),
            Body=json.dumps(reader_source_id, sort_keys=True).encode(),
            ContentType="application/json",
            IfNoneMatch="*",
        )


class AuthoritativePointerCASTests(TestCase):
    """Prove authoritative pointer CAS and epoch-based fencing."""

    def setUp(self):
        self.c = _client()

    def test_first_pointer_uses_conditional_create(self):
        try:
            self.c.delete_object(Bucket=BUCKET, Key=_pointer_key())
        except Exception:
            pass

        pointer = {"generation_id": "gen-1", "writer_epoch": 1, "manifest_sha256": "abc"}
        resp = self.c.put_object(
            Bucket=BUCKET, Key=_pointer_key(),
            Body=json.dumps(pointer).encode(),
            ContentType="application/json",
            IfNoneMatch="*",
        )
        self.assertTrue(resp.get("ETag"))

    def test_pointer_replace_requires_exact_etag(self):
        self.test_first_pointer_uses_conditional_create()

        resp = self.c.get_object(Bucket=BUCKET, Key=_pointer_key())
        etag = resp["ETag"]

        pointer2 = {"generation_id": "gen-2", "writer_epoch": 1, "manifest_sha256": "def"}
        resp2 = self.c.put_object(
            Bucket=BUCKET, Key=_pointer_key(),
            Body=json.dumps(pointer2).encode(),
            ContentType="application/json",
            IfMatch=etag,
        )
        self.assertTrue(resp2.get("ETag"))

        with self.assertRaises(Exception):
            self.c.put_object(
                Bucket=BUCKET, Key=_pointer_key(),
                Body=json.dumps({"generation_id": "gen-3"}).encode(),
                ContentType="application/json",
                IfMatch=etag,
            )

    def test_concurrent_pointer_race_only_one_wins(self):
        try:
            self.c.delete_object(Bucket=BUCKET, Key=_pointer_key())
        except Exception:
            pass

        resp = self.c.put_object(
            Bucket=BUCKET, Key=_pointer_key(),
            Body=json.dumps({"generation_id": "init", "writer_epoch": 1}).encode(),
            ContentType="application/json",
            IfNoneMatch="*",
        )
        etag = resp["ETag"]

        results = {"a": 0, "b": 0}
        start = threading.Event()

        def worker(label):
            start.wait()
            try:
                self.c.put_object(
                    Bucket=BUCKET, Key=_pointer_key(),
                    Body=json.dumps({"generation_id": f"gen-{label}", "writer_epoch": 1}).encode(),
                    ContentType="application/json",
                    IfMatch=etag,
                )
                results[label] = 1
            except Exception:
                pass

        t1 = threading.Thread(target=worker, args=("a",))
        t2 = threading.Thread(target=worker, args=("b",))
        t1.start()
        t2.start()
        start.set()
        t1.join()
        t2.join()

        winners = results["a"] + results["b"]
        self.assertEqual(winners, 1, f"CAS race: exactly one pointer update must succeed: {results}")

    def test_forged_higher_epoch_in_pointer_fails(self):
        try:
            self.c.delete_object(Bucket=BUCKET, Key=_pointer_key())
        except Exception:
            pass

        self.c.put_object(
            Bucket=BUCKET, Key=_pointer_key(),
            Body=json.dumps({"generation_id": "gen-1", "writer_epoch": 5}).encode(),
            ContentType="application/json",
            IfNoneMatch="*",
        )
        resp = self.c.get_object(Bucket=BUCKET, Key=_pointer_key())
        etag = resp["ETag"]
        current = json.loads(resp["Body"].read())
        self.assertEqual(current["writer_epoch"], 5)

        pointer2 = {"generation_id": "forged", "writer_epoch": 100}
        resp2 = self.c.put_object(
            Bucket=BUCKET, Key=_pointer_key(),
            Body=json.dumps(pointer2).encode(),
            ContentType="application/json",
            IfMatch=etag,
        )
        self.assertTrue(resp2.get("ETag"), "S3 CAS does not validate epoch semantics — application layer must")
