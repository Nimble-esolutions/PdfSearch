"""Backward-compatible alias for the canonical settings module.

Production now uses one settings implementation with DATA_ROOT-based paths.
"""

from .settings import *  # noqa: F401,F403
