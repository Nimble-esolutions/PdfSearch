#!/usr/bin/env bash
set -euo pipefail

cd /app/flowdocs

python - <<'PY'
import os
import time

import boto3

client = boto3.client(
    "s3",
    endpoint_url=os.environ["ARTIFACT_VAULT_ENDPOINT"],
    region_name=os.environ["ARTIFACT_VAULT_REGION"],
    aws_access_key_id=os.environ["ARTIFACT_VAULT_ACCESS_KEY"],
    aws_secret_access_key=os.environ["ARTIFACT_VAULT_SECRET_KEY"],
)
bucket = os.environ["ARTIFACT_VAULT_BUCKET"]
for attempt in range(60):
    try:
        client.head_bucket(Bucket=bucket)
        break
    except Exception:
        try:
            client.create_bucket(Bucket=bucket)
            break
        except Exception:
            if attempt == 59:
                raise
            time.sleep(1)
PY

python manage.py test \
  integration_tests.test_s3_primitives \
  integration_tests.test_vaultops_lifecycle \
  --noinput --verbosity=2
