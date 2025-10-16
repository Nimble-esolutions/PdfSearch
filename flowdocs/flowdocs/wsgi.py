"""
WSGI config for flowdocs project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os
import sys
from pathlib import Path

# Add the project root to Python path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from django.core.wsgi import get_wsgi_application

# Set the settings module based on environment
if os.getenv('DJANGO_SETTINGS_MODULE'):
    # Use environment variable if set
    pass
elif os.getenv('DEBUG', 'True').lower() == 'false':
    # Production environment - use production settings
    if os.path.exists('/app/flowdocs/flowdocs/settings_production.py'):
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'flowdocs.flowdocs.settings_production')
    elif os.path.exists('/app/flowdocs/settings_production.py'):
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'flowdocs.settings_production')
    else:
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'flowdocs.settings')
else:
    # Development environment - use regular settings
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'flowdocs.flowdocs.settings')

application = get_wsgi_application()
