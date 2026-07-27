"""Isolated settings used only by the maintenance candidate subprocess."""

import os
from pathlib import Path

from .settings import *  # noqa: F403

_workspace = Path(os.environ["MAINTENANCE_WORKSPACE_ROOT"]).resolve()
DATA_ROOT = _workspace
ACTIVE_RUNTIME = None
RUNTIME_GENERATION_ID = ""
RUNTIME_MANIFEST_DIGEST = ""
DATABASES["default"]["NAME"] = str(_workspace / "db.sqlite3")  # noqa: F405
MEDIA_ROOT = _workspace / "media"
FAISS_INDEX_DIR = _workspace / "faiss_indexes"
CHROMA_DIR = _workspace / "chroma_db"
PDF_CACHE_DIR = _workspace / "pdf_cache"
BACKUP_DIR = _workspace / "backups"
RECOVERY_SET_ROOT = _workspace / "recovery-sets"
VAULT_SYNC_ENABLED = False
VAULT_MUTATION_TRACKING_ENABLED = False
LOCAL_INDEX_MAINTENANCE_ENABLED = True
MAINTENANCE_CANDIDATE_EXECUTION = True
