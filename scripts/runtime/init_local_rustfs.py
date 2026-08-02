#!/usr/bin/env python3
"""Create the canonical local RustFS bucket with bounded, redacted retries."""

from __future__ import annotations

import os
import sys
import time
from urllib.parse import urlparse


LOCAL_HOSTS = {"rustfs", "localhost", "127.0.0.1", "::1"}


def validate_local_endpoint(endpoint: str) -> None:
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in LOCAL_HOSTS:
        raise ValueError("rustfs_endpoint_not_local")


def ensure_bucket(client, bucket: str, *, deadline: float, sleep=time.sleep) -> None:
    attempt = 0
    while True:
        attempt += 1
        try:
            client.head_bucket(Bucket=bucket)
            return
        except Exception:
            try:
                client.create_bucket(Bucket=bucket)
                return
            except Exception as exc:
                if time.monotonic() >= deadline:
                    raise RuntimeError("rustfs_bucket_initialization_timeout") from exc
                sleep(min(2 ** min(attempt, 4), 10))


def main() -> int:
    endpoint = os.environ.get("DEV_RUSTFS_ENDPOINT", "http://rustfs:9000")
    bucket = os.environ.get("DEV_RUSTFS_BUCKET", "pdfsearch-dev")
    access_key = os.environ.get("DEV_RUSTFS_ACCESS_KEY", "")
    secret_key = os.environ.get("DEV_RUSTFS_SECRET_KEY", "")
    try:
        validate_local_endpoint(endpoint)
        if not access_key or not secret_key:
            raise ValueError("rustfs_credentials_incomplete")
        import boto3

        client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name="us-east-1",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )
        timeout = max(1, int(os.environ.get("DEV_RUSTFS_INIT_TIMEOUT_SECONDS", "120")))
        ensure_bucket(client, bucket, deadline=time.monotonic() + timeout)
    except Exception as exc:
        reason = str(exc)
        if reason not in {
            "rustfs_endpoint_not_local",
            "rustfs_credentials_incomplete",
            "rustfs_bucket_initialization_timeout",
        }:
            reason = "rustfs_bucket_initialization_failed"
        print(reason, file=sys.stderr)
        return 1
    print("rustfs_bucket_ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
