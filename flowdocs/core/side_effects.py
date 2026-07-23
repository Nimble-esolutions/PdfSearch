"""Central side-effect safety layer.

Ensures production-derived non-production environments cannot accidentally
send email, process payments, invoke webhooks, or perform other external
actions. Uses the environment identity model to gate all outbound actions.
"""

from __future__ import annotations

from dataclasses import dataclass

from .environment import (
    AppEnv, EnvironmentIdentity, ExternalSideEffectsMode,
)


@dataclass(frozen=True)
class SideEffectPolicy:
    """Resolved side-effect policy based on environment identity."""

    email_enabled: bool = False
    email_backend: str = "django.core.mail.backends.dummy.EmailBackend"
    payment_enabled: bool = False
    webhook_enabled: bool = False
    sms_enabled: bool = False
    analytics_enabled: bool = False
    indexers_allowed: bool = False
    notification_enabled: bool = False

    @classmethod
    def from_identity(cls, identity: EnvironmentIdentity) -> "SideEffectPolicy":
        if identity.external_side_effects == ExternalSideEffectsMode.ENABLED:
            return cls(
                email_enabled=True,
                email_backend="django.core.mail.backends.smtp.EmailBackend",
                payment_enabled=True,
                webhook_enabled=True,
                sms_enabled=True,
                analytics_enabled=True,
                indexers_allowed=True,
                notification_enabled=True,
            )
        if identity.external_side_effects == ExternalSideEffectsMode.SANDBOX:
            return cls(
                email_enabled=True,
                email_backend="django.core.mail.backends.console.EmailBackend",
                payment_enabled=False,
                webhook_enabled=False,
                sms_enabled=False,
                analytics_enabled=False,
                indexers_allowed=False,
                notification_enabled=False,
            )
        return cls(
            email_enabled=False,
            email_backend="django.core.mail.backends.dummy.EmailBackend",
        )

    @property
    def production_email_ok(self) -> bool:
        return self.email_enabled and self.email_backend not in (
            "django.core.mail.backends.console.EmailBackend",
            "django.core.mail.backends.dummy.EmailBackend",
        )


def resolve_email_backend(identity: EnvironmentIdentity) -> str:
    """Resolve the Django EMAIL_BACKEND setting from environment identity."""
    policy = SideEffectPolicy.from_identity(identity)
    return policy.email_backend


def is_email_allowed(identity: EnvironmentIdentity) -> bool:
    """Check if email sending is permitted in this environment."""
    policy = SideEffectPolicy.from_identity(identity)
    return policy.email_enabled


def validate_production_safety(identity: EnvironmentIdentity) -> list[str]:
    """Return safety violations that must be addressed before startup.

    In non-production environments with production data, we check that
    production credentials aren't present when external effects aren't blocked.
    """
    from django.conf import settings
    errors: list[str] = []

    if identity.is_production:
        return errors

    if identity.data_mode.value in ("s3-restore", "s3-pinned", "exact-production", "sanitized-production"):
        policy = SideEffectPolicy.from_identity(identity)

        if policy.production_email_ok:
            email_host = getattr(settings, "EMAIL_HOST", "")
            if email_host:
                errors.append(
                    "EMAIL_HOST is configured but external side effects are not blocked "
                    "in a production-derived non-production environment. "
                    "Set EXTERNAL_SIDE_EFFECTS_MODE=disabled or sandbox."
                )

    return errors
