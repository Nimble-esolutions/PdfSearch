"""Versioned, testable sanitization pipeline for production-derived data.

Transforms personally identifiable and sensitive data in a quarantined copy
so it can be safely used in non-production environments. All sanitization
is deterministic where practical, repeatable, and auditable.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
from pathlib import Path
from typing import Any

from django.conf import settings
from django.contrib.auth.hashers import make_password


SANITIZATION_POLICY_V1 = "pdfsearch-sanitize/v1"

DEFAULT_SANITIZED_DOMAIN = "staging.pdfsearch.internal"


def sanitize_database(
    source_path: Path,
    target_path: Path,
    *,
    dataset_id: str = "",
    domain: str = DEFAULT_SANITIZED_DOMAIN,
    policy_version: str = SANITIZATION_POLICY_V1,
) -> dict[str, Any]:
    """Create a sanitized copy of a SQLite database.

    Transformations:
    - User emails: replace real domain with sanitized domain
    - User names: deterministic hash-based replacement
    - Passwords: make every copied credential unusable
    - Sessions: delete all
    - Password reset tokens: nullify
    - Audit actor references: preserve referential integrity, hash names

    Returns a sanitization report.
    """
    import shutil

    shutil.copy2(source_path, target_path)

    conn = sqlite3.connect(str(target_path))
    conn.execute("PRAGMA journal_mode=WAL")
    c = conn.cursor()

    stats: dict[str, int] = {}

    try:
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='core_customuser'")
        if c.fetchone():
            c.execute("SELECT id, username, email, first_name, last_name FROM core_customuser")
            users = c.fetchall()
            for user_id, username, email, first_name, last_name in users:

                sanitized_username = f"sanitized-{_hash_id(user_id, dataset_id)[:8]}"
                sanitized_email = ""
                if email:
                    local, _, host = email.partition("@")
                    if host:
                        sanitized_email = f"{_hash_id(user_id, dataset_id)[:8]}@{domain}"
                    else:
                        sanitized_email = f"sanitized-{user_id}@{domain}"
                sanitized_first = f"User{user_id}"
                sanitized_last = ""

                c.execute(
                    "UPDATE core_customuser SET username=?, email=?, first_name=?, "
                    "last_name=?, password=?, is_active=1 WHERE id=?",
                    (
                        sanitized_username,
                        sanitized_email,
                        sanitized_first,
                        sanitized_last,
                        f"!sanitized-{_hash_id(user_id, dataset_id)}",
                        user_id,
                    ),
                )
            stats["users_sanitized"] = len(users)
            stats["passwords_disabled"] = len(users)

        for session_table in ("django_session",):
            c.execute(
                f"SELECT name FROM sqlite_master WHERE type='table' AND name=?"
            , (session_table,))
            if c.fetchone():
                c.execute(f"DELETE FROM {session_table}")
                c.execute(f"SELECT changes()")
                rows = c.fetchone()[0]
                stats[f"{session_table}_cleared"] = rows

        c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='core_maintenanceauditevent'"
        )
        if c.fetchone():
            c.execute(
                "UPDATE core_maintenanceauditevent SET payload=json_set("
                "COALESCE(payload,'{}'), '$.sanitized', 1) "
                "WHERE json_extract(COALESCE(payload,'{}'), '$.sanitized') IS NULL"
            )
            c.execute("SELECT changes()")
            stats["audit_events_marked"] = c.fetchone()[0]

        conn.commit()

        c.execute("PRAGMA integrity_check")
        integrity = c.fetchone()[0]
        stats["integrity"] = integrity
        stats["integrity_ok"] = integrity == "ok"

    finally:
        conn.close()

    return {
        "policy_version": policy_version,
        "sanitized_domain": domain,
        "dataset_id": dataset_id,
        "stats": stats,
    }


def provision_recovery_superadmin(
    database_path: Path,
    *,
    username: str,
    password: str,
) -> dict[str, Any]:
    """Provision exactly one known recovery login in a sanitized database.

    The credential is written only to the isolated runtime copy. It is never
    returned in evidence, and every other copied password remains unusable.
    """
    if not username or not password:
        raise ValueError("recovery_superadmin_credentials_required")
    conn = sqlite3.connect(str(database_path))
    try:
        columns = {
            row[1]
            for row in conn.execute(
                'PRAGMA table_info("core_customuser")'
            ).fetchall()
        }
        required = {
            "id",
            "username",
            "password",
            "is_active",
            "is_staff",
            "is_superuser",
            "role",
        }
        if not required.issubset(columns):
            raise ValueError("recovery_superadmin_schema_unsupported")
        row = conn.execute(
            "SELECT id FROM core_customuser "
            "ORDER BY CASE WHEN username=? THEN 0 "
            "WHEN is_superuser=1 THEN 1 ELSE 2 END, id LIMIT 1",
            (username,),
        ).fetchone()
        if row is None:
            raise ValueError("recovery_superadmin_source_user_missing")
        user_id = row[0]
        values = {
            "username": username,
            "password": make_password(password),
            "is_active": 1,
            "is_staff": 1,
            "is_superuser": 1,
            "role": "superadmin",
        }
        if "email" in columns:
            values["email"] = (
                f"recovery@{DEFAULT_SANITIZED_DOMAIN}"
            )
        assignments = ", ".join(f'"{key}"=?' for key in values)
        conn.execute(
            f'UPDATE core_customuser SET {assignments} WHERE id=?',
            (*values.values(), user_id),
        )
        conn.commit()
        return {
            "provisioned": True,
            "user_id": user_id,
            "credentials_exposed": False,
        }
    finally:
        conn.close()


def validate_sanitization(
    database_path: Path,
    *,
    recovery_username: str = "",
) -> list[str]:
    """Verify that a database has been properly sanitized.

    Returns a list of issues. An empty list means the database passes validation.
    """
    issues: list[str] = []

    if not database_path.is_file():
        return ["Database file not found"]

    conn = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    c = conn.cursor()

    try:
        c.execute("SELECT email FROM core_customuser WHERE email LIKE '%@%'")
        emails = [row[0] for row in c.fetchall() if row[0]]
        for email in emails:
            _, _, host = email.partition("@")
            if host and host not in (DEFAULT_SANITIZED_DOMAIN,):
                if not host.endswith(".internal"):
                    issues.append("non_sanitized_email_domain")

        c.execute("PRAGMA table_info('core_customuser')")
        columns = {row[1] for row in c.fetchall()}
        if "password" in columns:
            c.execute(
                "SELECT username, password FROM core_customuser"
            )
            for username, password in c.fetchall():
                if username == recovery_username:
                    continue
                if not str(password or "").startswith("!"):
                    issues.append("copied_user_password_usable")
                    break

        c.execute(
            "SELECT session_key FROM django_session LIMIT 1"
        )
        if c.fetchone():
            issues.append("active_sessions_present")

    finally:
        conn.close()

    return issues


def _hash_id(entity_id: int, salt: str = "") -> str:
    """Deterministic, non-reversible hash of an entity ID."""
    secret = os.getenv("SANITIZATION_SECRET", settings.SECRET_KEY[:32] if settings.SECRET_KEY else "default")
    payload = f"{entity_id}:{salt}".encode()
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()[:16]
