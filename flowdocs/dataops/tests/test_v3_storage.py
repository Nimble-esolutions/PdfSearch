"""Credential-reference and immutable-write tests for v3 storage."""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
import unittest

from dataops.v3_storage import (
    V3StorageError,
    put_bytes_immutable,
    put_file_immutable,
    resolve_s3_credentials,
    verify_remote_object,
)


class MissingObject(Exception):
    response = {"Error": {"Code": "NoSuchKey"}}


class FakeS3:
    def __init__(self, *, preserve_metadata=True, titlecase_metadata=False):
        self.objects = {}
        self.puts = []
        self.preserve_metadata = preserve_metadata
        self.titlecase_metadata = titlecase_metadata

    def head_object(self, *, Bucket, Key):
        try:
            item = self.objects[(Bucket, Key)]
        except KeyError as exc:
            raise MissingObject() from exc
        metadata = dict(item["metadata"]) if self.preserve_metadata else {}
        if self.titlecase_metadata:
            metadata = {name.title(): value for name, value in metadata.items()}
        return {
            "ContentLength": len(item["body"]),
            "Metadata": metadata,
        }

    def put_object(self, *, Bucket, Key, Body, Metadata, **kwargs):
        if (Bucket, Key) in self.objects and kwargs.get("IfNoneMatch") == "*":
            raise RuntimeError("precondition failed")
        body = Body.read() if hasattr(Body, "read") else bytes(Body)
        self.objects[(Bucket, Key)] = {"body": body, "metadata": dict(Metadata)}
        self.puts.append((Bucket, Key))
        return {"ETag": "etag"}

    def get_object(self, *, Bucket, Key):
        try:
            item = self.objects[(Bucket, Key)]
        except KeyError as exc:
            raise MissingObject() from exc
        from io import BytesIO

        return {"Body": BytesIO(item["body"])}


class V3StorageTests(unittest.TestCase):
    def test_transition_environment_credentials_resolve_only_inside_worker(self):
        credentials = resolve_s3_credentials(
            "env://ARTIFACT_VAULT",
            environment={
                "ARTIFACT_VAULT_ACCESS_KEY": "access",
                "ARTIFACT_VAULT_SECRET_KEY": "secret",
            },
        )
        self.assertEqual(credentials["aws_access_key_id"], "access")
        self.assertEqual(credentials["aws_secret_access_key"], "secret")

    def test_missing_transition_credentials_fail_with_typed_code(self):
        with self.assertRaisesRegex(V3StorageError, "credential_unavailable"):
            resolve_s3_credentials("env://ARTIFACT_VAULT", environment={})

    def test_sdk_default_uses_the_standard_provider_chain(self):
        self.assertEqual(resolve_s3_credentials("aws-sdk://default"), {})

    def test_arbitrary_credential_file_paths_are_refused(self):
        with self.assertRaisesRegex(V3StorageError, "credential_reference_outside_secret_root"):
            resolve_s3_credentials("file:///tmp/credentials")

    def test_immutable_bytes_upload_once_then_reuse(self):
        client = FakeS3()
        body = b"manifest"
        digest = hashlib.sha256(body).hexdigest()
        first = put_bytes_immutable(
            client,
            bucket="bucket",
            key="manifest.json",
            body=body,
            sha256=digest,
            content_type="application/json",
        )
        second = put_bytes_immutable(
            client,
            bucket="bucket",
            key="manifest.json",
            body=body,
            sha256=digest,
            content_type="application/json",
        )
        self.assertEqual((first, second), ("uploaded", "reused"))
        self.assertEqual(len(client.puts), 1)

    def test_same_key_with_different_digest_is_an_integrity_failure(self):
        client = FakeS3()
        client.objects[("bucket", "blob")] = {
            "body": b"old",
            "metadata": {"sha256": "2" * 64},
        }
        with self.assertRaisesRegex(V3StorageError, "immutable_object_conflict"):
            put_bytes_immutable(
                client,
                bucket="bucket",
                key="blob",
                body=b"new",
                sha256=hashlib.sha256(b"new").hexdigest(),
                content_type="application/octet-stream",
            )

    def test_missing_provider_metadata_uses_content_hash_readback(self):
        client = FakeS3(preserve_metadata=False)
        body = b"metadata-optional"
        digest = hashlib.sha256(body).hexdigest()
        first = put_bytes_immutable(
            client,
            bucket="bucket",
            key="blob",
            body=body,
            sha256=digest,
            content_type="application/octet-stream",
        )
        second = put_bytes_immutable(
            client,
            bucket="bucket",
            key="blob",
            body=body,
            sha256=digest,
            content_type="application/octet-stream",
        )
        self.assertEqual((first, second), ("uploaded", "reused"))

    def test_provider_metadata_keys_are_case_insensitive(self):
        client = FakeS3(titlecase_metadata=True)
        body = b"case-normalized-metadata"
        digest = hashlib.sha256(body).hexdigest()
        first = put_bytes_immutable(
            client,
            bucket="bucket",
            key="blob",
            body=body,
            sha256=digest,
            content_type="application/octet-stream",
        )
        second = put_bytes_immutable(
            client,
            bucket="bucket",
            key="blob",
            body=body,
            sha256=digest,
            content_type="application/octet-stream",
        )
        self.assertEqual((first, second), ("uploaded", "reused"))

    def test_missing_provider_metadata_does_not_hide_content_conflict(self):
        client = FakeS3(preserve_metadata=False)
        client.objects[("bucket", "blob")] = {
            "body": b"tampered",
            "metadata": {},
        }
        expected = b"expected"
        with self.assertRaisesRegex(
            V3StorageError,
            "remote_object_digest_mismatch",
        ):
            put_bytes_immutable(
                client,
                bucket="bucket",
                key="blob",
                body=expected,
                sha256=hashlib.sha256(expected).hexdigest(),
                content_type="application/octet-stream",
            )

    def test_file_upload_streams_and_reuses_verified_object(self):
        client = FakeS3()
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "db.sqlite3"
            path.write_bytes(b"sqlite")
            first = put_file_immutable(
                client,
                bucket="bucket",
                key="blob",
                path=path,
                sha256=hashlib.sha256(b"sqlite").hexdigest(),
                size=6,
            )
            second = put_file_immutable(
                client,
                bucket="bucket",
                key="blob",
                path=path,
                sha256=hashlib.sha256(b"sqlite").hexdigest(),
                size=6,
            )
        self.assertEqual((first, second), ("uploaded", "reused"))

    def test_remote_verification_reads_content_not_only_metadata(self):
        client = FakeS3()
        body = b"verified"
        digest = hashlib.sha256(body).hexdigest()
        client.objects[("bucket", "blob")] = {
            "body": body,
            "metadata": {"sha256": digest},
        }
        verify_remote_object(
            client,
            bucket="bucket",
            key="blob",
            sha256=digest,
            size=len(body),
        )
        client.objects[("bucket", "blob")]["body"] = b"tampered"
        with self.assertRaisesRegex(V3StorageError, "remote_object_digest_mismatch"):
            verify_remote_object(
                client,
                bucket="bucket",
                key="blob",
                sha256=digest,
                size=len(body),
            )


if __name__ == "__main__":
    unittest.main()
