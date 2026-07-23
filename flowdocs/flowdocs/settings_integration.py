"""Integration test settings with file-backed SQLite.

Imports all production settings and overrides only what's needed to run
file-based integration tests against real MinIO and Redis.

Run with caller-provided environment variables:

  DJANGO_SETTINGS_MODULE=flowdocs.settings_integration \
  APP_ENV=production \
  DATASET_ID=app-pub-test \
  BACKUP_ROLE=writer \
  ... \
  python manage.py test integration_tests.test_app_publication --no-input
"""

import os
import tempfile
from pathlib import Path

INTEGRATION_ROOT = Path(
    os.environ.get(
        "PDFSEARCH_INTEGRATION_ROOT",
        tempfile.mkdtemp(prefix="pdfsearch-integration-"),
    )
).resolve()
INTEGRATION_ROOT.mkdir(parents=True, exist_ok=True)

DB_PATH = INTEGRATION_ROOT / "integration.sqlite3"
MEDIA_PATH = INTEGRATION_ROOT / "media"
FAISS_PATH = INTEGRATION_ROOT / "faiss_indexes"
CHROMA_PATH = INTEGRATION_ROOT / "chroma_db"
BACKUP_PATH = INTEGRATION_ROOT / "backups"

for p in [MEDIA_PATH, FAISS_PATH, CHROMA_PATH, BACKUP_PATH]:
    p.mkdir(parents=True, exist_ok=True)

os.environ["SQLITE_DB_PATH"] = str(DB_PATH)
os.environ["DATA_ROOT"] = str(INTEGRATION_ROOT / "data")
os.environ["MEDIA_ROOT"] = str(MEDIA_PATH)
os.environ["FAISS_INDEX_DIR"] = str(FAISS_PATH)
os.environ["CHROMA_DIR"] = str(CHROMA_PATH)
os.environ["BACKUP_DIR"] = str(BACKUP_PATH)
os.environ.setdefault("ALLOW_INSECURE_DEFAULTS", "1")
os.environ.setdefault("SECRET_KEY", "integration-test-key-not-for-production")
os.environ.setdefault("DATA_BOOTSTRAP_MODE", "empty")
os.environ.setdefault("EXTERNAL_SIDE_EFFECTS_MODE", "disabled")
os.environ.setdefault("DATA_MODE", "local")

from flowdocs.settings import *  # noqa: E402, F403

DATABASES["default"]["NAME"] = str(DB_PATH)
DATABASES["default"]["TEST"] = {
    "NAME": str(DB_PATH),
    "MIGRATE": True,
}

assert DATABASES["default"]["NAME"] == str(DB_PATH), (
    f"DATABASES NAME mismatch: {DATABASES['default']['NAME']} != {DB_PATH}"
)
assert DATABASES["default"]["TEST"]["NAME"] == str(DB_PATH)
assert Path(DATABASES["default"]["NAME"]).resolve() == DB_PATH
