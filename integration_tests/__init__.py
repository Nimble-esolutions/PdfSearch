import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "flowdocs.settings")
os.environ.setdefault("ALLOW_INSECURE_DEFAULTS", "1")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATA_MODE", "empty")
os.environ.setdefault("DATASET_ID", "integration-test")
os.environ.setdefault("BACKUP_ROLE", "disabled")
import django
django.setup()
