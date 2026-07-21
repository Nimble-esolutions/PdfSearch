"""Compatibility wrapper for the canonical ``flowdocs.settings`` module.

The project historically contained two settings files. Runtime and management
commands use the package settings so there is one source of truth for paths,
security defaults, and database configuration.
"""

from flowdocs.settings import *  # noqa: F401,F403
