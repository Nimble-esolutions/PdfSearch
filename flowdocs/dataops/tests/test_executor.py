import json
import tempfile
import unittest
from pathlib import Path

from dataops.config import ResolvedProfile
from dataops.executor import RestoreExecutionError, stage_restore
from dataops.package import build_manifest


class _Body:
    def __init__(self, value): self.value = value
    def read(self, *_): return self.value


class _Client:
    def __init__(self, objects): self.objects = objects
    def get_object(self, *, Bucket, Key): return {"Body": _Body(self.objects[Key])}


class RestoreExecutorTests(unittest.TestCase):
    def setUp(self):
        self.profile = ResolvedProfile("production", "Production", "restore", "https://s3.example", "bucket", "us-east-1", "dataset", "prod", "DATAOPS")

    def test_stages_and_verifies_generation(self):
        payload = build_manifest(
            release_id="release-1", dataset_id="dataset", source={"source_id": "prod"},
            identity={}, counts={"objects": 1}, files=[{"key": "datasets/dataset/generations/release-1/media/a.pdf", "sha256": "0" * 64}],
        ).raw
        # The contract's file digest is intentionally corrected for the fixture.
        payload["files"][0]["sha256"] = ""  # invalid entry proves fail-closed
        manifest = json.dumps(payload).encode()
        client = _Client({"datasets/dataset/generations/release-1/manifest.json": manifest})
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(RestoreExecutionError):
                stage_restore(client, self.profile, "release-1", temp)

    def test_existing_destination_is_not_overwritten(self):
        data = b"pdf"
        import hashlib
        digest = hashlib.sha256(data).hexdigest()
        payload = build_manifest(
            release_id="release-1", dataset_id="dataset", source={"source_id": "prod"},
            identity={}, counts={"objects": 1}, files=[{"key": "datasets/dataset/generations/release-1/media/a.pdf", "sha256": digest}],
        ).raw
        client = _Client({
            "datasets/dataset/generations/release-1/manifest.json": json.dumps(payload).encode(),
            "datasets/dataset/generations/release-1/media/a.pdf": data,
        })
        with tempfile.TemporaryDirectory() as temp:
            Path(temp, "restore-release-1").mkdir()
            with self.assertRaisesRegex(RestoreExecutionError, "restore_destination_exists"):
                stage_restore(client, self.profile, "release-1", temp)
