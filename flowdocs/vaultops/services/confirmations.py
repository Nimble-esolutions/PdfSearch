import hashlib
import hmac
import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from vaultops.models import ConfirmationChallenge


ALLOWED_ACTIONS = {
    "activate_workspace": "ACTIVATE",
    "rollback_runtime": "ROLLBACK",
    "promote_generation": "PROMOTE",
    "retire_generation": "RETIRE",
    "unretire_generation": "UNRETIRE",
}
TARGET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")


class ConfirmationError(RuntimeError):
    reason_code = "confirmation_invalid"

    def __init__(self, reason_code=None):
        self.reason_code = reason_code or self.reason_code
        super().__init__(self.reason_code)


def _phrase_digest(phrase, salt):
    payload = f"{salt}:{phrase}".encode("utf-8")
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()


def issue_confirmation(
    *,
    actor_id,
    action,
    target,
    state_digest,
    expires_seconds=300,
):
    verb = ALLOWED_ACTIONS.get(action)
    if (
        verb is None
        or not TARGET_RE.fullmatch(target or "")
        or not re.fullmatch(r"[0-9a-f]{64}", state_digest or "")
    ):
        raise ConfirmationError("confirmation_target_invalid")
    phrase = f"{verb} {target}"
    salt = secrets.token_hex(24)
    challenge = ConfirmationChallenge.objects.create(
        actor_id=actor_id,
        action=action,
        target=target,
        state_digest=state_digest,
        phrase_salt=salt,
        phrase_digest=_phrase_digest(phrase, salt),
        expires_at=timezone.now()
        + timedelta(seconds=max(60, min(int(expires_seconds), 600))),
    )
    return challenge, phrase


def _validate_confirmation(
    *,
    challenge_id,
    actor_id,
    action,
    target,
    state_digest,
    phrase,
    consume,
):
    now = timezone.now()
    with transaction.atomic(using="control"):
        try:
            challenge = ConfirmationChallenge.objects.select_for_update().get(
                public_id=challenge_id
            )
        except (
            ConfirmationChallenge.DoesNotExist,
            ValueError,
        ) as exc:
            raise ConfirmationError("confirmation_not_found") from exc
        if challenge.used_at is not None:
            raise ConfirmationError("confirmation_replayed")
        if challenge.expires_at <= now:
            raise ConfirmationError("confirmation_expired")
        if (
            challenge.actor_id != actor_id
            or challenge.action != action
            or challenge.target != target
        ):
            raise ConfirmationError("confirmation_identity_mismatch")
        if not hmac.compare_digest(
            challenge.state_digest, state_digest or ""
        ):
            raise ConfirmationError("stale_state")
        supplied = _phrase_digest((phrase or "").strip(), challenge.phrase_salt)
        if not hmac.compare_digest(challenge.phrase_digest, supplied):
            raise ConfirmationError("confirmation_phrase_mismatch")
        confirmation_digest = hashlib.sha256(
            (
                f"{challenge.public_id}:{challenge.state_digest}:"
                f"{challenge.phrase_digest}"
            ).encode("utf-8")
        ).hexdigest()
        if consume:
            challenge.used_at = now
            challenge.save(update_fields=["used_at"])
        return confirmation_digest


def validate_confirmation(
    *,
    challenge_id,
    actor_id,
    action,
    target,
    state_digest,
    phrase,
):
    """Validate a challenge without consuming it or authorizing mutation."""
    return _validate_confirmation(
        challenge_id=challenge_id,
        actor_id=actor_id,
        action=action,
        target=target,
        state_digest=state_digest,
        phrase=phrase,
        consume=False,
    )


def consume_confirmation(
    *,
    challenge_id,
    actor_id,
    action,
    target,
    state_digest,
    phrase,
):
    return _validate_confirmation(
        challenge_id=challenge_id,
        actor_id=actor_id,
        action=action,
        target=target,
        state_digest=state_digest,
        phrase=phrase,
        consume=True,
    )
