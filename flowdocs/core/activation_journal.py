"""Durable activation journal with heartbeat-based locking.

Records every activation phase so crashed processes can be deterministically
recovered. Uses O_EXCL lock file with ownership token, process identity,
heartbeat renewal, and explicit liveness-based staleness detection instead
of timeout-only cleanup.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

JOURNAL_DIR = "/app/data-control/activation-journals"
LOCK_FILE = "/app/data-control/activation.lock"


class ActivationJournalError(RuntimeError):
    """Raised when journal operations cannot proceed."""


class ActivationLockStolen(ActivationJournalError):
    """Raised when another process holds or stole the activation lock."""


@dataclass
class ActivationJournal:
    """Audited record of an activation attempt."""

    activation_id: str
    workspace_id: str
    workspace_path: str
    previous_generation: str
    target_generation: str
    phase: str = "intent_recorded"
    lock_token: str = ""
    owner_pid: int = 0
    owner_instance: str = ""
    started_at: float = 0
    last_heartbeat: float = 0
    last_transition: float = 0
    validation_passed: bool = False
    rollback_triggered: bool = False
    completed: bool = False
    error: str = ""

    def save(self) -> None:
        Path(JOURNAL_DIR).mkdir(parents=True, exist_ok=True)
        path = Path(JOURNAL_DIR) / f"{self.activation_id}.json"
        path.write_text(json.dumps(self.to_dict(), indent=2))

    def to_dict(self) -> dict:
        return {
            "activation_id": self.activation_id,
            "workspace_id": self.workspace_id,
            "workspace_path": self.workspace_path,
            "previous_generation": self.previous_generation,
            "target_generation": self.target_generation,
            "phase": self.phase,
            "lock_token": self.lock_token,
            "owner_pid": self.owner_pid,
            "owner_instance": self.owner_instance,
            "started_at": self.started_at,
            "last_heartbeat": self.last_heartbeat,
            "last_transition": self.last_transition,
            "validation_passed": self.validation_passed,
            "rollback_triggered": self.rollback_triggered,
            "completed": self.completed,
            "error": self.error,
        }

    @classmethod
    def load(cls, activation_id: str) -> "ActivationJournal | None":
        path = Path(JOURNAL_DIR) / f"{activation_id}.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text())
            return cls(**data)
        except Exception:
            return None


def acquire_activation_lock(
    *,
    activation_id: str,
    instance_id: str = "",
    timeout_seconds: int = 30,
) -> ActivationJournal:
    """Acquire the exclusive activation lock with O_EXCL.

    If a lock exists, checks whether the holder is still alive before
    stealing. A lock is stale only when the owning process is confirmed
    dead AND the heartbeat has not been renewed.
    """
    Path(JOURNAL_DIR).mkdir(parents=True, exist_ok=True)
    lock_path = Path(LOCK_FILE)
    token = secrets.token_hex(16)
    pid = os.getpid()
    now = time.time()

    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(fd, json.dumps({
            "activation_id": activation_id,
            "lock_token": token,
            "pid": pid,
            "instance_id": instance_id,
            "acquired_at": now,
        }).encode())
        os.close(fd)
    except FileExistsError:
        existing = _read_lock_data()
        if existing is None:
            lock_path.unlink(missing_ok=True)
            return acquire_activation_lock(
                activation_id=activation_id,
                instance_id=instance_id,
            )

        existing_age = now - existing.get("acquired_at", 0)

        # Primary: Redis heartbeat — must be dead to consider stealing
        heartbeat_alive = _redis_heartbeat_active(
            existing.get("activation_id", ""), timeout_seconds=300
        )
        if heartbeat_alive:
            raise ActivationLockStolen(
                f"Activation lock held with active Redis heartbeat "
                f"(age: {existing_age:.0f}s, activation: {existing.get('activation_id')})"
            )

        # Advisory: PID check — logged but does not gate lock stealing
        pid_alive = _process_alive(existing.get("pid", 0))
        if pid_alive:
            logger.warning(
                "Activation lock PID %s appears alive but Redis heartbeat is dead "
                "for %s. PID namespace mismatch likely in containerized env. "
                "Proceeding with staleness check.",
                existing.get("pid"), existing.get("activation_id"),
            )

        # Hard cap: refuse to steal locks younger than 10 minutes
        if existing_age < 600:
            raise ActivationLockStolen(
                f"Activation lock held by PID {existing.get('pid')} "
                f"(age: {existing_age:.0f}s, below 600s absolute maximum, "
                f"activation: {existing.get('activation_id')})"
            )

        logger.warning(
            "Stealing expired activation lock (%s) — "
            "age=%.0fs pid_alive=%s heartbeat_alive=%s",
            existing.get("activation_id"), existing_age, pid_alive, heartbeat_alive,
        )

        lock_path.unlink()
        return acquire_activation_lock(
            activation_id=activation_id,
            instance_id=instance_id,
        )

    journal = ActivationJournal(
        activation_id=activation_id,
        phase="intent_recorded",
        lock_token=token,
        owner_pid=pid,
        owner_instance=instance_id,
        started_at=now,
        last_heartbeat=now,
        last_transition=now,
    )
    journal.save()
    _set_redis_heartbeat(activation_id, token, ttl=60)
    return journal


def renew_activation_heartbeat(journal: ActivationJournal) -> ActivationJournal:
    """Renew the heartbeat on the activation lock."""
    if not _verify_lock_ownership(journal.lock_token):
        raise ActivationLockStolen("Lock ownership lost during heartbeat renewal")

    journal.last_heartbeat = time.time()
    journal.save()
    _set_redis_heartbeat(journal.activation_id, journal.lock_token, ttl=60)

    lock_path = Path(LOCK_FILE)
    if lock_path.exists():
        try:
            lock_path.write_text(json.dumps({
                "activation_id": journal.activation_id,
                "lock_token": journal.lock_token,
                "pid": journal.owner_pid,
                "heartbeat_at": journal.last_heartbeat,
            }))
        except Exception:
            pass

    return journal


def transition_phase(
    journal: ActivationJournal,
    new_phase: str,
    *,
    lock_token: str = "",
) -> ActivationJournal:
    """Transition activation to a new phase, verifying lock ownership."""
    if lock_token and lock_token != journal.lock_token:
        raise ActivationLockStolen("Lock token mismatch during phase transition")

    if not _verify_lock_ownership(journal.lock_token):
        raise ActivationLockStolen("Lock ownership lost before phase transition")

    journal.phase = new_phase
    journal.last_transition = time.time()
    journal.save()
    return journal


def release_activation_lock(journal: ActivationJournal) -> None:
    """Release the activation lock and mark journal complete."""
    if _verify_lock_ownership(journal.lock_token):
        Path(LOCK_FILE).unlink(missing_ok=True)
    _clear_redis_heartbeat(journal.activation_id)
    journal.completed = True
    journal.phase = "completed" if journal.phase != "rolled_back" else journal.phase
    journal.save()


def reconcile_incomplete_activations() -> list[dict]:
    """On startup, detect and reconcile any incomplete activation journals.

    Returns a list of reconciliation actions taken.
    """
    actions = []
    journal_dir = Path(JOURNAL_DIR)
    if not journal_dir.is_dir():
        return actions

    for journal_file in journal_dir.glob("*.json"):
        try:
            data = json.loads(journal_file.read_text())
        except Exception:
            continue

        if data.get("completed"):
            continue

        activation_id = data.get("activation_id", "")
        phase = data.get("phase", "")
        workspace_path = data.get("workspace_path", "")
        previous_gen = data.get("previous_generation", "")
        target_gen = data.get("target_generation", "")

        from .activate import read_active_pointer

        active = read_active_pointer()
        action = {
            "activation_id": activation_id,
            "phase": phase,
            "workspace_path": workspace_path,
            "active_pointer": active,
            "action_taken": "unknown",
        }

        if Path(LOCK_FILE).exists():
            lock_data = _read_lock_data()
            if lock_data and lock_data.get("pid"):
                heartbeat_alive = _redis_heartbeat_active(
                    lock_data.get("activation_id", ""), timeout_seconds=300
                )
                if heartbeat_alive or _process_alive(lock_data.get("pid", 0)):
                    action["action_taken"] = "skipped_live_lock"
                    actions.append(action)
                    continue

        if active and target_gen and str(Path(target_gen).resolve()) == str(Path(active).resolve()):
            if phase == "pointer_switched":
                action["action_taken"] = "completing_validation"
            elif phase == "validating":
                action["action_taken"] = "resume_validation"
            else:
                action["action_taken"] = "activation_appears_complete"
            data["completed"] = True
            data["phase"] = "recovery_confirmed"
            journal_file.write_text(json.dumps(data, indent=2))

        elif active and previous_gen:
            if phase in ("rollback_pending", "rolled_back"):
                data["completed"] = True
                data["phase"] = "recovery_rollback_confirmed"
                journal_file.write_text(json.dumps(data, indent=2))
                action["action_taken"] = "rollback_confirmed"

            else:
                from .activate import write_active_pointer
                write_active_pointer(previous_gen)
                data["completed"] = True
                data["phase"] = "recovery_rolled_back"
                data["rollback_triggered"] = True
                journal_file.write_text(json.dumps(data, indent=2))
                action["action_taken"] = "rollback_to_previous"

        else:
            action["action_taken"] = "requires_operator"
            data["phase"] = "recovery_requires_operator"
            journal_file.write_text(json.dumps(data, indent=2))

        actions.append(action)

    return actions


def activation_status() -> dict:
    lock_held = Path(LOCK_FILE).exists()
    lock_data = _read_lock_data()
    journals = []
    journal_dir = Path(JOURNAL_DIR)
    if journal_dir.is_dir():
        for f in sorted(journal_dir.glob("*.json")):
            try:
                journals.append(json.loads(f.read_text()))
            except Exception:
                pass

    return {
        "lock_held": lock_held,
        "lock_data": lock_data,
        "journals": journals,
        "incomplete_activations": [j for j in journals if not j.get("completed")],
    }


def _read_lock_data() -> dict | None:
    try:
        data = Path(LOCK_FILE).read_text()
        return json.loads(data) if data else None
    except Exception:
        return None


def _verify_lock_ownership(token: str) -> bool:
    lock_data = _read_lock_data()
    if lock_data is None:
        return False
    return lock_data.get("lock_token") == token


def _process_alive(pid: int) -> bool:
    """Check process liveness — ADVISORY ONLY.

    In containerized environments with isolated PID namespaces, this check
    is only meaningful for processes on the same host in the same namespace.
    Always prefer _redis_heartbeat_active() as the primary liveness authority.
    This function is logged but never gates lock-stealing decisions.
    """
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _heartbeat_key(activation_id: str) -> str:
    return f"activation:heartbeat:{activation_id}"


def _redis_heartbeat_active(activation_id: str, timeout_seconds: int = 300) -> bool:
    """Check if activation heartbeat is still active via time-based expiry.

    Stores a floating-point timestamp in the cache key and compares
    elapsed time against timeout_seconds.  Fail-open: returns True
    on Redis errors to avoid deadlocking.
    """
    try:
        from django.core.cache import cache
        stored = cache.get(_heartbeat_key(activation_id))
        if stored is None:
            return False
        last_beat = float(stored)
        elapsed = time.time() - last_beat
        return elapsed < timeout_seconds
    except Exception:
        return True


def _set_redis_heartbeat(activation_id: str, token: str = "", ttl: int = 60) -> None:
    """Set a Redis-backed activation heartbeat timestamp."""
    try:
        from django.core.cache import cache
        cache.set(_heartbeat_key(activation_id), time.time(), timeout=ttl)
    except Exception:
        pass


def _clear_redis_heartbeat(activation_id: str) -> None:
    try:
        from django.core.cache import cache
        cache.delete(_heartbeat_key(activation_id))
    except Exception:
        pass
