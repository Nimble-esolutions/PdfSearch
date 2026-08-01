"""Data Operations control plane.

This package is deliberately small: profiles, durable operation state and
format-aware backup/restore helpers live here while execution remains in the
existing maintenance worker.
"""

default_app_config = "dataops.apps.DataOpsConfig"

