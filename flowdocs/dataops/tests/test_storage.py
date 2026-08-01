import unittest

from dataops.config import ResolvedProfile
from dataops.storage import StorageConfigurationError, observe_manifests, validate_endpoint


class FakeBody:
    def __init__(self, value):
        self.value = value

    def read(self, _limit):
        return self.value


class FakeClient:
    def list_objects_v2(self, **_kwargs):
        return {"Contents": [{"Key": "datasets/prod/release/manifest.json", "ETag": '"etag"'}]}

    def get_object(self, **_kwargs):
        import json
        value = {"manifest_version": 1, "read_only": True, "release_id": "r1", "source": {"dataset_id": "prod"}, "files": [{"key": "db.sqlite3"}]}
        return {"Body": FakeBody(json.dumps(value).encode())}


class StorageTests(unittest.TestCase):
    def test_endpoint_policy_and_manifest_observation(self):
        self.assertEqual(validate_endpoint("https://objects.example.invalid"), "https://objects.example.invalid")
        with self.assertRaises(StorageConfigurationError):
            validate_endpoint("http://objects.example.invalid")
        profile = ResolvedProfile("prod", "Production", "backup", "https://objects.example.invalid", "bucket", "us-east-1", "prod", "source", "OPS")
        observations = observe_manifests(FakeClient(), profile)
        self.assertEqual(observations[0].inspection.release_id, "r1")


if __name__ == "__main__":
    unittest.main()
