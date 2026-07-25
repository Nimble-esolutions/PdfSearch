from vaultops.models import VaultAuditEvent


def append_event(
    *,
    action,
    result,
    correlation_id,
    actor_id=None,
    actor_name="",
    job_public_id=None,
    reason_code="",
    before_state=None,
    after_state=None,
    confirmation_digest="",
    safe_error_code="",
    evidence=None,
):
    """Append redacted lifecycle evidence to the stable control database."""
    return VaultAuditEvent.objects.create(
        actor_id=actor_id,
        actor_name=actor_name,
        correlation_id=correlation_id,
        job_public_id=job_public_id,
        action=action,
        reason_code=reason_code,
        before_state=before_state or {},
        after_state=after_state or {},
        confirmation_digest=confirmation_digest,
        result=result,
        safe_error_code=safe_error_code,
        evidence=evidence or {},
    )
