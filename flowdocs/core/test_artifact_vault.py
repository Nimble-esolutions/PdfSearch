import hashlib
import json
from io import BytesIO

from django.test import SimpleTestCase

from .artifact_vault import (
    ArtifactVault,
    ArtifactVaultDisabled,
    ArtifactVaultIntegrityError,
    VaultConfig,
)


class FakeBody(BytesIO):
    pass


class FakeClient:
    def __init__(self):
        self.objects = {}

    def put_object(self, **kwargs):
        self.objects[kwargs["Key"]] = {
            "body": kwargs["Body"],
            "metadata": kwargs["Metadata"],
            "content_type": kwargs["ContentType"],
        }
        return {"ETag": '"fake-etag"'}

    def get_object(self, **kwargs):
        item = self.objects[kwargs["Key"]]
        return {
            "Body": FakeBody(item["body"]),
            "Metadata": item["metadata"],
            "ContentType": item["content_type"],
        }

    def head_object(self, **kwargs):
        item = self.objects[kwargs["Key"]]
        return {
            "ContentLength": len(item["body"]),
            "Metadata": item["metadata"],
            "ContentType": item["content_type"],
            "ETag": '"fake-etag"',
        }

    def list_objects_v2(self, **kwargs):
        keys = sorted(key for key in self.objects if key.startswith(kwargs["Prefix"]))
        return {"Contents": [{"Key": key} for key in keys], "IsTruncated": False}


class ArtifactVaultTests(SimpleTestCase):
    def setUp(self):
        self.client = FakeClient()
        self.vault = ArtifactVault(
            VaultConfig(
                enabled=True,
                endpoint="http://rustfs.test",
                bucket="artifacts",
                region="us-east-1",
                access_key="access",
                secret_key="secret",
            ),
            client=self.client,
        )

    def test_disabled_vault_fails_closed_without_client(self):
        vault = ArtifactVault(VaultConfig(enabled=False))
        with self.assertRaises(ArtifactVaultDisabled):
            vault.put_pdf(b"%PDF-1.7")

    def test_pdf_key_is_content_addressed_and_round_trips_with_checksum(self):
        payload = b"%PDF-1.7 immutable"
        metadata = self.vault.put_pdf(BytesIO(payload))

        expected = hashlib.sha256(payload).hexdigest()
        self.assertEqual(metadata.key, f"pdfs/sha256/{expected}.pdf")
        self.assertEqual(self.vault.get(metadata.key, expected_sha256=expected), payload)
        self.assertEqual(self.vault.head(metadata.key).sha256, expected)

    def test_put_rejects_wrong_checksum_before_upload(self):
        with self.assertRaises(ArtifactVaultIntegrityError):
            self.vault.put_pdf(b"payload", expected_sha256="0" * 64)
        self.assertEqual(self.client.objects, {})

    def test_pdf_key_cannot_claim_different_content(self):
        key = ArtifactVault.pdf_object_key("0" * 64)
        with self.assertRaises(ArtifactVaultIntegrityError):
            self.vault.put(key, b"payload")

    def test_get_rejects_body_tampering(self):
        metadata = self.vault.put_pdf(b"original")
        self.client.objects[metadata.key]["body"] = b"tampered"
        with self.assertRaises(ArtifactVaultIntegrityError):
            self.vault.get(metadata.key)

    def test_manifest_upload_and_listing(self):
        payload = b"%PDF-1.7"
        manifest = {
            "release_id": "release-2026-07-22",
            "files": [
                {
                    "path": "media/pdfs/example.pdf",
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "bytes": len(payload),
                }
            ],
        }
        metadata = self.vault.put_manifest(manifest)
        self.assertEqual(metadata.key, "manifests/release-2026-07-22.json")
        self.assertEqual(len(self.vault.list_manifests()), 1)
        self.assertEqual(
            json.loads(self.vault.get_manifest("release-2026-07-22"))["release_id"],
            "release-2026-07-22",
        )
        self.assertEqual(self.vault.head_manifest("release-2026-07-22").key, metadata.key)
