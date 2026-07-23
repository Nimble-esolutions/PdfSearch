"""Real S3 CAS primitives against MinIO — foundation for all distributed tests."""

from __future__ import annotations

import json
import os
import secrets
import threading
import time

import boto3
from django.test import TestCase

BUCKET = "pdfsearch-test"
ENDPOINT = "http://pdfsearch-minio:9000"
REGION = "us-east-1"


def _client():
    return boto3.client(
        "s3",
        endpoint_url=ENDPOINT,
        region_name=REGION,
        aws_access_key_id="minioadmin",
        aws_secret_access_key="minioadmin",
    )


class S3ConditionalCreateTests(TestCase):
    """Prove If-None-Match: * works for conditional object creation."""

    def setUp(self):
        self.c = _client()
        self.prefix = f"test-cond-create-{secrets.token_hex(4)}"

    def tearDown(self):
        try:
            resp = self.c.list_objects_v2(Bucket=BUCKET, Prefix=self.prefix)
            objs = [{"Key": o["Key"]} for o in resp.get("Contents", [])]
            if objs:
                self.c.delete_objects(Bucket=BUCKET, Delete={"Objects": objs, "Quiet": True})
        except Exception:
            pass

    def test_conditional_create_succeeds_when_object_missing(self):
        key = f"{self.prefix}/test.json"
        self.c.put_object(
            Bucket=BUCKET, Key=key, Body=b"v1",
            IfNoneMatch="*",
        )
        resp = self.c.get_object(Bucket=BUCKET, Key=key)
        self.assertEqual(resp["Body"].read(), b"v1")

    def test_second_conditional_create_fails(self):
        key = f"{self.prefix}/test.json"
        self.c.put_object(Bucket=BUCKET, Key=key, Body=b"v1", IfNoneMatch="*")
        with self.assertRaises(Exception):
            self.c.put_object(Bucket=BUCKET, Key=key, Body=b"v2", IfNoneMatch="*")

    def test_etag_is_captured_consistently(self):
        key = f"{self.prefix}/etag.json"
        resp = self.c.put_object(Bucket=BUCKET, Key=key, Body=b"content")
        etag1 = resp.get("ETag", "")
        self.assertTrue(etag1, "ETag must be present")

        resp2 = self.c.head_object(Bucket=BUCKET, Key=key)
        self.assertEqual(resp2.get("ETag", ""), etag1)

    def test_conditional_replace_succeeds_with_exact_etag(self):
        key = f"{self.prefix}/replace.json"
        resp1 = self.c.put_object(Bucket=BUCKET, Key=key, Body=b"v1")
        etag = resp1["ETag"]

        resp2 = self.c.put_object(
            Bucket=BUCKET, Key=key, Body=b"v2",
            IfMatch=etag,
        )
        self.assertTrue(resp2.get("ETag"))

        body = self.c.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        self.assertEqual(body, b"v2")

    def test_replace_fails_with_stale_etag(self):
        key = f"{self.prefix}/stale.json"
        resp1 = self.c.put_object(Bucket=BUCKET, Key=key, Body=b"v1")

        resp2 = self.c.put_object(
            Bucket=BUCKET, Key=key, Body=b"v1.5",
            IfMatch=resp1["ETag"],
        )

        with self.assertRaises(Exception):
            self.c.put_object(
                Bucket=BUCKET, Key=key, Body=b"v2",
                IfMatch=resp1["ETag"],
            )

        body = self.c.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        self.assertEqual(body, b"v1.5", "Body should remain at v1.5, stale write rejected")


class S3ConcurrencyTests(TestCase):
    """Prove CAS works under real concurrent access."""

    def setUp(self):
        self.c = _client()
        self.prefix = f"test-race-{secrets.token_hex(4)}"

    def tearDown(self):
        try:
            resp = self.c.list_objects_v2(Bucket=BUCKET, Prefix=self.prefix)
            objs = [{"Key": o["Key"]} for o in resp.get("Contents", [])]
            if objs:
                self.c.delete_objects(Bucket=BUCKET, Delete={"Objects": objs, "Quiet": True})
        except Exception:
            pass

    def test_concurrent_conditional_create_only_one_succeeds(self):
        key = f"{self.prefix}/race.json"
        results = {"a": False, "b": False, "errors": 0}

        def worker(label):
            try:
                self.c.put_object(
                    Bucket=BUCKET, Key=key, Body=label.encode(),
                    IfNoneMatch="*",
                )
                results[label] = True
            except Exception:
                results["errors"] += 1

        t1 = threading.Thread(target=worker, args=("a",))
        t2 = threading.Thread(target=worker, args=("b",))
        t1.start(); t2.start()
        t1.join(); t2.join()

        winners = sum(1 for v in [results["a"], results["b"]] if v)
        self.assertEqual(winners, 1, f"Exactly one conditional create must succeed, got: {results}")

    def test_concurrent_etag_replace_only_one_succeeds(self):
        key = f"{self.prefix}/etag-race.json"
        resp = self.c.put_object(Bucket=BUCKET, Key=key, Body=b"init")
        etag = resp["ETag"]

        results = {"a": 0, "b": 0}
        barrier = threading.Barrier(2, timeout=5)

        def worker(label):
            barrier.wait()
            try:
                self.c.put_object(
                    Bucket=BUCKET, Key=key, Body=label.encode(),
                    IfMatch=etag,
                )
                results[label] = 1
            except Exception:
                pass

        t1 = threading.Thread(target=worker, args=("a",))
        t2 = threading.Thread(target=worker, args=("b",))
        t1.start(); t2.start()
        t1.join(); t2.join()

        winners = results["a"] + results["b"]
        self.assertEqual(winners, 1, f"Exactly one CAS replace must succeed: {results}")
