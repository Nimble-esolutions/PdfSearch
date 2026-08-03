"""Canonical Django application-to-database ownership for recovery evidence."""

from __future__ import annotations


DEFAULT_DATABASE_ALIAS = "default"
CONTROL_DATABASE_ALIAS = "control"
CONTROL_APP_LABELS = frozenset({"dataops", "vaultops"})


def database_alias_for_app(app_label: str) -> str:
    """Return the database that owns migrations for ``app_label``.

    This explicit map is shared by routers and emergency recovery.  Depending
    on router fallback at migration-inspection time is unsafe because an app
    label without a model hint may otherwise default to every database.
    """

    return (
        CONTROL_DATABASE_ALIAS
        if str(app_label or "").strip().lower() in CONTROL_APP_LABELS
        else DEFAULT_DATABASE_ALIAS
    )
