"""Read-only proof of the isolated recovery superadmin account."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from django.conf import settings
from django.contrib.auth.hashers import check_password


class RecoveryAuthenticationError(RuntimeError):
    """Recovery authentication could not be proven without opening Django."""

    reason_code = "recovery_superadmin_unproven"


def verify_recovery_superadmin_database(
    database_path: Path,
    *,
    username: str | None = None,
    password: str | None = None,
) -> dict[str, str]:
    """Prove the configured recovery login using a raw read-only connection.

    The result is deliberately secret-free.  Callers that need a
    subsystem-specific exception should translate ``RecoveryAuthenticationError``
    without exposing the underlying credential or database row.
    """

    candidate = Path(database_path)
    username = (
        settings.ACTIVATION_RECOVERY_SUPERADMIN_USERNAME
        if username is None
        else username
    )
    password = (
        settings.ACTIVATION_RECOVERY_SUPERADMIN_PASSWORD
        if password is None
        else password
    )
    if (
        not username
        or not password
        or candidate.is_symlink()
        or not candidate.is_file()
    ):
        raise RecoveryAuthenticationError

    try:
        connection = sqlite3.connect(
            f"file:{candidate.resolve()}?mode=ro&immutable=1", uri=True
        )
        columns = {
            row[1]
            for row in connection.execute(
                'PRAGMA table_info("core_customuser")'
            ).fetchall()
        }
        required = {
            "username",
            "password",
            "is_active",
            "is_superuser",
            "role",
        }
        if not required.issubset(columns):
            raise RecoveryAuthenticationError
        row = connection.execute(
            "SELECT password, is_active, is_superuser, role "
            "FROM core_customuser WHERE username=? LIMIT 1",
            (username,),
        ).fetchone()
    except (sqlite3.Error, OSError) as exc:
        raise RecoveryAuthenticationError from exc
    finally:
        if "connection" in locals():
            connection.close()

    try:
        valid_password = bool(row) and check_password(password, row[0])
    except (TypeError, ValueError) as exc:
        raise RecoveryAuthenticationError from exc
    if (
        not row
        or not valid_password
        or not bool(row[1])
        or not bool(row[2])
        or row[3] != "superadmin"
    ):
        raise RecoveryAuthenticationError
    return {"state": "verified"}
