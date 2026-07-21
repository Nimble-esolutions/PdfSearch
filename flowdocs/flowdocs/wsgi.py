"""WSGI configuration for the FlowDocs project."""

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'flowdocs.settings')

from django.core.wsgi import get_wsgi_application  # noqa: E402

application = get_wsgi_application()
