"""Pure environment-direction policy for ordinary Vault operations."""

from __future__ import annotations

import enum
from dataclasses import dataclass


class Operation(enum.Enum):
    BACKUP = "backup"
    SYNC = "sync"
    PROMOTE = "promote"
    RESTORE = "restore"
    ACTIVATE = "activate"
    ROLLBACK = "rollback"


@dataclass(frozen=True)
class DirectionDecision:
    allowed: bool
    reason_code: str = ""
    local_dataset_id: str = ""
    remote_dataset_id: str = ""


@dataclass(frozen=True)
class EnvironmentDirectionPolicy:
    """Resolve operation direction from immutable server identity only.

    Feature flags, credentials, and artifact verification remain independent
    gates. This policy answers only whether an environment may perform an
    operation in the requested dataset direction.
    """

    identity: object

    @classmethod
    def from_identity(cls, identity):
        return cls(identity=identity)

    def decision(self, operation, *, remote_dataset_id=""):
        operation = Operation(operation)
        local_dataset_id = str(
            getattr(self.identity, "dataset_id", "") or ""
        )
        remote_dataset_id = str(remote_dataset_id or "")
        app_env = getattr(self.identity, "app_env", None)
        environment_name = str(getattr(app_env, "value", app_env) or "")
        production_flag = bool(
            getattr(self.identity, "is_production", False)
        )
        production_posture = (
            production_flag or environment_name == "production"
        )
        deployment_id = str(
            getattr(self.identity, "deployment_id", "") or ""
        )

        if operation in {Operation.ACTIVATE, Operation.ROLLBACK}:
            if not local_dataset_id or not deployment_id:
                return DirectionDecision(
                    False,
                    "environment_identity_incomplete",
                    local_dataset_id,
                    remote_dataset_id,
                )
            allowed = (
                not production_posture
                and environment_name == "staging"
            )
            return DirectionDecision(
                allowed,
                ""
                if allowed
                else "production_activation_disabled"
                if production_posture
                else "staging_activation_disabled",
                local_dataset_id,
                remote_dataset_id,
            )

        if not local_dataset_id:
            return DirectionDecision(
                False,
                "environment_identity_incomplete",
                local_dataset_id,
                remote_dataset_id,
            )

        if operation in {
            Operation.BACKUP,
            Operation.SYNC,
            Operation.PROMOTE,
        }:
            authoritative_dataset_id = str(
                getattr(
                    self.identity,
                    "authoritative_dataset_id",
                    local_dataset_id,
                )
                or ""
            )
            target_dataset_id = remote_dataset_id or local_dataset_id
            allowed = (
                bool(
                    getattr(
                        self.identity,
                        "is_authoritative_writer",
                        False,
                    )
                )
                and production_flag
                and environment_name == "production"
                and authoritative_dataset_id == local_dataset_id
                and target_dataset_id == local_dataset_id
            )
            return DirectionDecision(
                allowed,
                "" if allowed else "writer_environment_required",
                local_dataset_id,
                target_dataset_id,
            )

        if operation == Operation.RESTORE:
            nonproduction_environment = environment_name in {
                "staging",
                "development",
                "review",
                "test",
            }
            allowed = (
                not production_posture
                and nonproduction_environment
                and bool(remote_dataset_id)
                and remote_dataset_id != local_dataset_id
            )
            reason_code = ""
            if not allowed:
                reason_code = (
                    "restore_dataset_mismatch"
                    if remote_dataset_id == local_dataset_id
                    and bool(remote_dataset_id)
                    else "restore_environment_required"
                )
            return DirectionDecision(
                allowed,
                reason_code,
                local_dataset_id,
                remote_dataset_id,
            )

        raise AssertionError(f"Unhandled operation: {operation.value}")
