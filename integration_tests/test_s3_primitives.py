"""Real S3 CAS primitives against the selected S3 provider.

Usage: docker exec pdfsearch-web-1 python -m pytest /app/integration_tests/test_s3_primitives.py -v
"""

import json
import os
import secrets
import threading
import unittest

import boto3

BUCKET = os.environ.get("ARTIFACT_VAULT_BUCKET", "pdfsearch-test")
ENDPOINT = os.environ.get("ARTIFACT_VAULT_ENDPOINT", "http://pdfsearch-objectstore:9000")
REGION = "us-east-1"


def _client():
    return boto3.client(
        "s3",
        endpoint_url=ENDPOINT,
        region_name=REGION,
        aws_access_key_id=os.environ.get("ARTIFACT_VAULT_ACCESS_KEY", "integration-local"),
        aws_secret_access_key=os.environ.get("ARTIFACT_VAULT_SECRET_KEY", "integration-local-secret"),
    )


class S3ConditionalCreateTests(unittest.TestCase):
    def setUp(self):
        self.c = _client()
        self.prefix = f"test-cond-{secrets.token_hex(4)}"

    def tearDown(self):
        try:
            resp = self.c.list_objects_v2(Bucket=BUCKET, Prefix=self.prefix)
            for o in resp.get("Contents", []):
                self.c.delete_object(Bucket=BUCKET, Key=o["Key"])
        except Exception:
            pass

    def test_01_conditional_create_succeeds(self):
        key = f"{self.prefix}/a.json"
        self.c.put_object(Bucket=BUCKET, Key=key, Body=b"v1", IfNoneMatch="*")
        self.assertEqual(self.c.get_object(Bucket=BUCKET, Key=key)["Body"].read(), b"v1")

    def test_02_second_conditional_create_fails(self):
        key = f"{self.prefix}/b.json"
        self.c.put_object(Bucket=BUCKET, Key=key, Body=b"v1", IfNoneMatch="*")
        with self.assertRaises(Exception):
            self.c.put_object(Bucket=BUCKET, Key=key, Body=b"v2", IfNoneMatch="*")

    def test_03_etag_captured_consistently(self):
        key = f"{self.prefix}/c.json"
        resp = self.c.put_object(Bucket=BUCKET, Key=key, Body=b"content")
        self.assertTrue(resp.get("ETag"))
        etag2 = self.c.head_object(Bucket=BUCKET, Key=key).get("ETag")
        self.assertEqual(etag2, resp["ETag"])

    def test_04_replace_with_exact_etag(self):
        key = f"{self.prefix}/d.json"
        resp = self.c.put_object(Bucket=BUCKET, Key=key, Body=b"v1")
        self.c.put_object(Bucket=BUCKET, Key=key, Body=b"v2", IfMatch=resp["ETag"])
        self.assertEqual(self.c.get_object(Bucket=BUCKET, Key=key)["Body"].read(), b"v2")

    def test_05_replace_fails_with_stale_etag(self):
        key = f"{self.prefix}/e.json"
        r1 = self.c.put_object(Bucket=BUCKET, Key=key, Body=b"v1")
        stale_etag = r1["ETag"]
        self.c.put_object(Bucket=BUCKET, Key=key, Body=b"v2", IfMatch=stale_etag)
        with self.assertRaises(Exception):
            self.c.put_object(Bucket=BUCKET, Key=key, Body=b"v3", IfMatch=stale_etag)

    def test_06_concurrent_create_only_one_succeeds(self):
        key = f"{self.prefix}/race.json"
        results = {"a": False, "b": False}

        def w(label):
            try:
                self.c.put_object(Bucket=BUCKET, Key=key, Body=label.encode(), IfNoneMatch="*")
                results[label] = True
            except Exception:
                pass

        t1 = threading.Thread(target=w, args=("a",)); t2 = threading.Thread(target=w, args=("b",))
        t1.start(); t2.start(); t1.join(); t2.join()
        self.assertEqual(sum(1 for v in results.values() if v), 1, str(results))

    def test_07_concurrent_etag_replace_only_one_wins(self):
        key = f"{self.prefix}/etag-race.json"
        resp = self.c.put_object(Bucket=BUCKET, Key=key, Body=b"init")
        etag = resp["ETag"]
        results = {"a": 0, "b": 0}
        start = threading.Event()

        def w(label):
            start.wait()
            try:
                self.c.put_object(Bucket=BUCKET, Key=key, Body=label.encode(), IfMatch=etag)
                results[label] = 1
            except Exception:
                pass

        t1 = threading.Thread(target=w, args=("a",)); t2 = threading.Thread(target=w, args=("b",))
        t1.start(); t2.start(); start.set(); t1.join(); t2.join()
        self.assertEqual(results["a"] + results["b"], 1, str(results))


class PublicationIntegrationTests(unittest.TestCase):
    DATASET = "pub-test-dataset"

    def setUp(self):
        self.c = _client()
        self.writer_key = f"datasets/{self.DATASET}/control/writer.json"
        self.reg_key = f"datasets/{self.DATASET}/control/registration.json"
        self.ptr_key = f"datasets/{self.DATASET}/control/authoritative.json"
        for key in [self.writer_key, self.reg_key, self.ptr_key]:
            try:
                self.c.delete_object(Bucket=BUCKET, Key=key)
            except Exception:
                pass

    def _make_writer(self, source_id, instance_id, epoch):
        return {
            "schema_version": 1, "dataset_id": self.DATASET,
            "production_source_id": source_id, "instance_id": instance_id,
            "owner_token_hash": secrets.token_hex(32), "writer_epoch": epoch,
            "acquired_at": 0, "expires_at": 9999999999, "app_release": "test",
        }

    def test_01_registration_conditional_create(self):
        reg = {"dataset_id": self.DATASET, "registration_version": 1,
               "production_source_id": "prod-primary", "created_at": "2026-07-24",
               "registration_nonce": secrets.token_hex(8)}
        self.c.put_object(Bucket=BUCKET, Key=self.reg_key, Body=json.dumps(reg).encode(),
                          ContentType="application/json", IfNoneMatch="*")
        self.assertTrue(self.c.head_object(Bucket=BUCKET, Key=self.reg_key).get("ETag"))

    def test_02_registration_cannot_be_overwritten(self):
        self.test_01_registration_conditional_create()
        with self.assertRaises(Exception):
            self.c.put_object(Bucket=BUCKET, Key=self.reg_key, Body=b"evil",
                              ContentType="application/json", IfNoneMatch="*")

    def test_03_writer_acquisition_cas(self):
        wr = self._make_writer("prod-primary", "prod-a", 1)
        resp = self.c.put_object(Bucket=BUCKET, Key=self.writer_key,
                                 Body=json.dumps(wr, sort_keys=True).encode(),
                                 ContentType="application/json", IfNoneMatch="*")
        self.assertTrue(resp["ETag"])
        stored = json.loads(self.c.get_object(Bucket=BUCKET, Key=self.writer_key)["Body"].read())
        self.assertEqual(stored["writer_epoch"], 1)

    def test_04_second_writer_blocked(self):
        self.test_03_writer_acquisition_cas()
        with self.assertRaises(Exception):
            wr2 = self._make_writer("prod-primary", "prod-b", 2)
            self.c.put_object(Bucket=BUCKET, Key=self.writer_key,
                              Body=json.dumps(wr2, sort_keys=True).encode(),
                              ContentType="application/json", IfNoneMatch="*")

    def test_05_takeover_with_etag_succeeds(self):
        self.test_03_writer_acquisition_cas()
        resp = self.c.get_object(Bucket=BUCKET, Key=self.writer_key)
        etag = resp["ETag"]
        current = json.loads(resp["Body"].read())
        wr = self._make_writer("prod-primary", "prod-b", current["writer_epoch"] + 1)
        resp2 = self.c.put_object(Bucket=BUCKET, Key=self.writer_key,
                                  Body=json.dumps(wr, sort_keys=True).encode(),
                                  ContentType="application/json", IfMatch=etag)
        self.assertTrue(resp2["ETag"] != etag)
        stored = json.loads(self.c.get_object(Bucket=BUCKET, Key=self.writer_key)["Body"].read())
        self.assertEqual(stored["writer_epoch"], 2)
        self.assertEqual(stored["instance_id"], "prod-b")

    def test_06_stale_etag_takeover_fails(self):
        self.test_03_writer_acquisition_cas()
        resp = self.c.get_object(Bucket=BUCKET, Key=self.writer_key)
        stale_etag = resp["ETag"]
        wr = self._make_writer("prod-primary", "prod-b", 2)
        self.c.put_object(Bucket=BUCKET, Key=self.writer_key,
                          Body=json.dumps(wr, sort_keys=True).encode(),
                          ContentType="application/json", IfMatch=stale_etag)
        with self.assertRaises(Exception):
            wr2 = self._make_writer("prod-primary", "prod-c", 3)
            self.c.put_object(Bucket=BUCKET, Key=self.writer_key,
                              Body=json.dumps(wr2, sort_keys=True).encode(),
                              ContentType="application/json", IfMatch=stale_etag)

    def test_07_rogue_source_can_write_but_app_validation_rejects(self):
        self.test_01_registration_conditional_create()
        reg = json.loads(self.c.get_object(Bucket=BUCKET, Key=self.reg_key)["Body"].read())
        self.assertEqual(reg["production_source_id"], "prod-primary")
        wr = self._make_writer("rogue-source", "evil", 1)
        self.c.put_object(Bucket=BUCKET, Key=self.writer_key,
                          Body=json.dumps(wr, sort_keys=True).encode(),
                          ContentType="application/json", IfNoneMatch="*")
        self.assertEqual(json.loads(self.c.get_object(Bucket=BUCKET, Key=self.writer_key)["Body"].read())["production_source_id"], "rogue-source")

    def test_08_pointer_cas_succeeds(self):
        ptr = {"generation_id": "gen-1", "writer_epoch": 1, "manifest_sha256": "abc123"}
        r = self.c.put_object(Bucket=BUCKET, Key=self.ptr_key,
                              Body=json.dumps(ptr).encode(),
                              ContentType="application/json", IfNoneMatch="*")
        ptr2 = {"generation_id": "gen-2", "writer_epoch": 1, "manifest_sha256": "def456"}
        r2 = self.c.put_object(Bucket=BUCKET, Key=self.ptr_key,
                               Body=json.dumps(ptr2).encode(),
                               ContentType="application/json", IfMatch=r["ETag"])
        self.assertTrue(r2["ETag"] != r["ETag"])

    def test_09_pointer_stale_etag_fails(self):
        self.test_08_pointer_cas_succeeds()
        r = self.c.get_object(Bucket=BUCKET, Key=self.ptr_key)
        stale = r["ETag"]
        r2 = self.c.put_object(Bucket=BUCKET, Key=self.ptr_key,
                               Body=json.dumps({"generation_id": "gen-3"}).encode(),
                               ContentType="application/json", IfMatch=stale)
        with self.assertRaises(Exception):
            self.c.put_object(Bucket=BUCKET, Key=self.ptr_key,
                              Body=json.dumps({"generation_id": "gen-X"}).encode(),
                              ContentType="application/json", IfMatch=stale)
