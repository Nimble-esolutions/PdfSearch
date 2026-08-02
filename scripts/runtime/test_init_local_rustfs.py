from __future__ import annotations

import unittest
from unittest.mock import Mock

from scripts.runtime.init_local_rustfs import ensure_bucket, validate_local_endpoint


class LocalRustfsInitializerTests(unittest.TestCase):
    def test_accepts_only_local_service_endpoints(self):
        for endpoint in ("http://rustfs:9000", "http://127.0.0.1:9000", "https://localhost:9000"):
            validate_local_endpoint(endpoint)
        with self.assertRaisesRegex(ValueError, "rustfs_endpoint_not_local"):
            validate_local_endpoint("https://storage.example.com")

    def test_existing_bucket_is_idempotent(self):
        client = Mock()
        ensure_bucket(client, "pdfsearch-dev", deadline=float("inf"))
        client.head_bucket.assert_called_once_with(Bucket="pdfsearch-dev")
        client.create_bucket.assert_not_called()

    def test_missing_bucket_is_created(self):
        client = Mock()
        client.head_bucket.side_effect = RuntimeError("missing sentinel-secret")
        ensure_bucket(client, "pdfsearch-dev", deadline=float("inf"))
        client.create_bucket.assert_called_once_with(Bucket="pdfsearch-dev")

    def test_deadline_failure_uses_bounded_reason(self):
        client = Mock()
        client.head_bucket.side_effect = RuntimeError("credential-value")
        client.create_bucket.side_effect = RuntimeError("permission-value")
        with self.assertRaisesRegex(RuntimeError, "rustfs_bucket_initialization_timeout") as error:
            ensure_bucket(client, "pdfsearch-dev", deadline=0, sleep=lambda _: None)
        self.assertNotIn("credential-value", str(error.exception))
        self.assertNotIn("permission-value", str(error.exception))


if __name__ == "__main__":
    unittest.main()
