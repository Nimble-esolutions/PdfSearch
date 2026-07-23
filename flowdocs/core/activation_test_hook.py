"""Test-only activation failure injection hook.

Provides a single central checkpoint that can be controlled by integration
tests to inject exceptions or process termination during activation.

All methods are no-ops when not enabled. Cannot be enabled in production.
"""

from __future__ import annotations

import os
import sys
from typing import Callable


_enabled = False
_checkpoints: dict[str, Callable] = {}


def enable() -> None:
    """Enable failure injection. Only callable from test code with explicit env."""
    if os.environ.get("APP_ENV") == "production" and not os.environ.get("ALLOW_INSECURE_DEFAULTS"):
        raise RuntimeError("Cannot enable failure injection in production")
    global _enabled
    _enabled = True


def disable() -> None:
    global _enabled
    _enabled = False


def checkpoint(name: str) -> None:
    """Called by activation code at named checkpoints.

    When enabled, invokes the registered injector function (if any)
    for this checkpoint. When disabled, is a zero-cost no-op.
    """
    if not _enabled:
        return
    if name in _checkpoints:
        _checkpoints[name]()


def set_injector(name: str, fn: Callable) -> None:
    """Register an injector for a named checkpoint.

    The injector is called inline at the checkpoint site. It may raise
    an exception or terminate the process to simulate a crash.
    """
    _checkpoints[name] = fn


def clear_all() -> None:
    _checkpoints.clear()
    disable()


def crash_process() -> None:
    """Immediate hard process exit, simulating SIGKILL."""
    os._exit(1)


def raise_failure(msg: str = "Injected activation failure") -> None:
    raise InjectedActivationFailure(msg)


class InjectedActivationFailure(RuntimeError):
    """Exception raised by test failure injection."""
