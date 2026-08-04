"""Outcome-based decisions for reversible document visibility changes.

This module deliberately excludes permanent deletion.  It centralizes the
policy needed by a future ``Remove from search`` endpoint while delegating the
actual, already-proven mutations to :mod:`core.maintenance`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from django.db import transaction

from ..maintenance import (
    MEDIA_QUARANTINE_REASONS,
    archive_pdf,
    deprecate_pdf,
    mark_pdf_unavailable,
    restore_pdf,
    restore_unavailable_pdf,
)
from ..models import PDFFile, SEARCHABLE_PDF_LIFECYCLES


MANAGE_LIFECYCLE_ROLES = frozenset({"admin", "superadmin"})
ORDINARY_LIFECYCLE_STATES = frozenset({"uploaded", "processing", "ready"})
HIDDEN_LIFECYCLE_STATES = frozenset({"deprecated", "archived", "unavailable"})
ALL_LIFECYCLE_STATES = ORDINARY_LIFECYCLE_STATES | HIDDEN_LIFECYCLE_STATES

_CASE_REFERENCE_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class LifecycleDecisionRejected(ValueError):
    """A safe, machine-classifiable lifecycle decision rejection."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class LifecyclePermissionDenied(LifecycleDecisionRejected):
    """The actor's application role cannot manage document lifecycles."""


class UnknownLifecycleReason(LifecycleDecisionRejected):
    """The requested outcome is not one of the supported reason codes."""


class InvalidLifecycleSourceState(LifecycleDecisionRejected):
    """The reason cannot be applied from the document's current state."""


class LifecycleInputRejected(LifecycleDecisionRejected):
    """Required evidence or operator input is missing or malformed."""


@dataclass(frozen=True)
class LifecyclePolicy:
    """Immutable policy for one human-facing removal outcome."""

    reason_code: str
    target_state: str
    allowed_source_states: frozenset[str]
    reversible: bool
    required_inputs: tuple[str, ...]
    recovery_operation: str
    recovery_requirements: tuple[str, ...]


@dataclass(frozen=True)
class UnavailabilityEvidence:
    """Evidence accepted by the existing unavailable-media contract.

    Exact recovery evidence is optional at quarantine time, as it is in the
    maintenance contract, but the digest and size must be supplied together.
    A later restore still requires durable exact evidence and matching bytes.
    """

    quarantine_reason: str
    case_reference: str
    expected_sha256: str = ""
    expected_size: int | str | None = None


@dataclass(frozen=True)
class DocumentLifecycleImpact:
    """Storage-safe impact preview; it never opens or deletes document media."""

    document_id: int | None
    reason_code: str
    source_state: str
    target_state: str
    file_reference_present: bool
    file_action: str
    index_marked_before: bool
    index_marked_after: bool
    folder_index_rebuild_required: bool
    search_visible_before: bool
    search_visible_after: bool
    folder_id: int | None
    folder_name: str | None
    folder_membership_action: str
    permanent_deletion: bool

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-ready representation for a future endpoint."""
        return {
            "document_id": self.document_id,
            "reason_code": self.reason_code,
            "source_state": self.source_state,
            "target_state": self.target_state,
            "file": {
                "reference_present": self.file_reference_present,
                "action": self.file_action,
            },
            "index": {
                "marked_before": self.index_marked_before,
                "marked_after": self.index_marked_after,
                "folder_rebuild_required": self.folder_index_rebuild_required,
            },
            "search_visibility": {
                "before": self.search_visible_before,
                "after": self.search_visible_after,
            },
            "folder": {
                "id": self.folder_id,
                "name": self.folder_name,
                "membership_action": self.folder_membership_action,
            },
            "permanent_deletion": self.permanent_deletion,
        }


@dataclass(frozen=True)
class LifecycleTransitionResult:
    """Normalized result returned by removal and recovery operations."""

    pdf: PDFFile
    source_state: str
    target_state: str
    changed: bool
    reason_code: str | None
    reversible: bool
    impact: DocumentLifecycleImpact | None


@dataclass(frozen=True)
class RecoveryDecision:
    """Contextual recovery contract for a currently hidden document."""

    source_state: str
    target_state: str | None
    operation: str
    required_evidence: tuple[str, ...]
    evidence_bound: bool


_POLICIES = {
    "superseded": LifecyclePolicy(
        reason_code="superseded",
        target_state="deprecated",
        allowed_source_states=ORDINARY_LIFECYCLE_STATES | {"deprecated"},
        reversible=True,
        required_inputs=(),
        recovery_operation="restore_to_uploaded",
        recovery_requirements=(),
    ),
    "historical_record": LifecyclePolicy(
        reason_code="historical_record",
        target_state="archived",
        allowed_source_states=ORDINARY_LIFECYCLE_STATES
        | {"deprecated", "archived"},
        reversible=True,
        required_inputs=(),
        recovery_operation="restore_to_uploaded",
        recovery_requirements=(),
    ),
    "source_temporarily_unavailable": LifecyclePolicy(
        reason_code="source_temporarily_unavailable",
        target_state="unavailable",
        allowed_source_states=ALL_LIFECYCLE_STATES,
        reversible=True,
        required_inputs=("quarantine_reason", "case_reference"),
        recovery_operation="verified_media_restore",
        recovery_requirements=(
            "stored_expected_sha256",
            "stored_expected_size",
            "stored_prior_lifecycle",
            "matching_local_media",
        ),
    ),
}

LIFECYCLE_POLICIES: Mapping[str, LifecyclePolicy] = MappingProxyType(_POLICIES)


def get_lifecycle_policy(reason_code: str) -> LifecyclePolicy:
    """Return the immutable policy for an explicit reason code."""
    normalized = str(reason_code or "").strip()
    try:
        return LIFECYCLE_POLICIES[normalized]
    except KeyError as exc:
        raise UnknownLifecycleReason(
            "unknown_reason_code",
            "Choose a supported document lifecycle reason.",
        ) from exc


def actor_can_manage_lifecycle(actor: Any) -> bool:
    """Match the application's role-based admin contract without view coupling."""
    return bool(
        actor is not None
        and getattr(actor, "is_authenticated", False)
        and getattr(actor, "role", None) in MANAGE_LIFECYCLE_ROLES
    )


def _require_actor_role(actor: Any) -> None:
    if not actor_can_manage_lifecycle(actor):
        raise LifecyclePermissionDenied(
            "lifecycle_permission_denied",
            "Administrator permission is required to change document visibility.",
        )


def _require_allowed_source(pdf: PDFFile, policy: LifecyclePolicy) -> None:
    if pdf.lifecycle not in policy.allowed_source_states:
        raise InvalidLifecycleSourceState(
            "invalid_source_state",
            f"Reason '{policy.reason_code}' cannot be applied from lifecycle "
            f"'{pdf.lifecycle}'.",
        )


def _coerce_unavailability_evidence(
    evidence: UnavailabilityEvidence | Mapping[str, Any] | None,
) -> UnavailabilityEvidence:
    if isinstance(evidence, UnavailabilityEvidence):
        value = evidence
    else:
        source = evidence or {}
        value = UnavailabilityEvidence(
            quarantine_reason=str(source.get("quarantine_reason", "")).strip(),
            case_reference=str(source.get("case_reference", "")).strip(),
            expected_sha256=str(source.get("expected_sha256", "")).strip().lower(),
            expected_size=source.get("expected_size"),
        )

    if not value.quarantine_reason or not value.case_reference:
        raise LifecycleInputRejected(
            "missing_required_input",
            "Unavailable documents require a verified reason and case reference.",
        )
    if value.quarantine_reason not in MEDIA_QUARANTINE_REASONS:
        raise LifecycleInputRejected(
            "invalid_quarantine_reason",
            "Choose an approved unavailable-media evidence reason.",
        )
    if not _CASE_REFERENCE_PATTERN.fullmatch(value.case_reference):
        raise LifecycleInputRejected(
            "invalid_case_reference",
            "The case reference format is invalid.",
        )

    digest = value.expected_sha256
    raw_size = "" if value.expected_size is None else str(value.expected_size).strip()
    if bool(digest) != bool(raw_size):
        raise LifecycleInputRejected(
            "incomplete_recovery_evidence",
            "Expected digest and size must be supplied together.",
        )
    if digest:
        if not _SHA256_PATTERN.fullmatch(digest):
            raise LifecycleInputRejected(
                "invalid_expected_sha256",
                "The expected media digest is invalid.",
            )
        try:
            parsed_size = int(raw_size)
        except (TypeError, ValueError) as exc:
            raise LifecycleInputRejected(
                "invalid_expected_size",
                "The expected media size is invalid.",
            ) from exc
        if parsed_size < 0 or parsed_size > 2**63 - 1:
            raise LifecycleInputRejected(
                "invalid_expected_size",
                "The expected media size is invalid.",
            )
        value = UnavailabilityEvidence(
            quarantine_reason=value.quarantine_reason,
            case_reference=value.case_reference,
            expected_sha256=digest,
            expected_size=parsed_size,
        )
    else:
        value = UnavailabilityEvidence(
            quarantine_reason=value.quarantine_reason,
            case_reference=value.case_reference,
        )
    return value


def build_impact_preview(
    pdf: PDFFile,
    reason_code: str,
) -> DocumentLifecycleImpact:
    """Describe the bounded effects of one removal outcome without mutation."""
    policy = get_lifecycle_policy(reason_code)
    _require_allowed_source(pdf, policy)
    folder = getattr(pdf, "folder", None) if pdf.folder_id is not None else None
    source_searchable = pdf.lifecycle in SEARCHABLE_PDF_LIFECYCLES
    return DocumentLifecycleImpact(
        document_id=pdf.pk,
        reason_code=policy.reason_code,
        source_state=pdf.lifecycle,
        target_state=policy.target_state,
        file_reference_present=bool(getattr(pdf.file, "name", "")),
        file_action="preserve",
        index_marked_before=bool(pdf.indexed),
        index_marked_after=False,
        folder_index_rebuild_required=bool(
            pdf.folder_id is not None and (pdf.indexed or source_searchable)
        ),
        search_visible_before=source_searchable,
        search_visible_after=False,
        folder_id=pdf.folder_id,
        folder_name=getattr(folder, "name", None),
        folder_membership_action="preserve",
        permanent_deletion=False,
    )


def remove_from_search(
    pdf: PDFFile,
    *,
    reason_code: str,
    actor: Any,
    evidence: UnavailabilityEvidence | Mapping[str, Any] | None = None,
) -> LifecycleTransitionResult:
    """Apply one approved removal outcome through existing maintenance services."""
    _require_actor_role(actor)
    policy = get_lifecycle_policy(reason_code)
    unavailable_evidence = None
    if policy.target_state == "unavailable":
        unavailable_evidence = _coerce_unavailability_evidence(evidence)

        # Tracked media transitions intentionally own their outer transaction
        # so source-mutation bookkeeping advances only after a committed media
        # change.  Do not wrap this call in another atomic block.
        current = PDFFile.objects.select_related("folder").get(pk=pdf.pk)
        _require_allowed_source(current, policy)
        impact = build_impact_preview(current, policy.reason_code)
        source_state = current.lifecycle
        outcome = mark_pdf_unavailable(
            current,
            requested_by=actor,
            expected_sha256=unavailable_evidence.expected_sha256,
            expected_size=unavailable_evidence.expected_size,
            reason=unavailable_evidence.quarantine_reason,
            case_reference=unavailable_evidence.case_reference,
        )
        return LifecycleTransitionResult(
            pdf=outcome.pdf,
            source_state=source_state,
            target_state=policy.target_state,
            changed=outcome.changed,
            reason_code=policy.reason_code,
            reversible=policy.reversible,
            impact=impact,
        )

    with transaction.atomic():
        current = (
            PDFFile.objects.select_for_update()
            .select_related("folder")
            .get(pk=pdf.pk)
        )
        _require_allowed_source(current, policy)
        impact = build_impact_preview(current, policy.reason_code)
        source_state = current.lifecycle

        if policy.target_state == "deprecated":
            changed = source_state != "deprecated"
            if changed:
                current = deprecate_pdf(current, requested_by=actor)
        elif policy.target_state == "archived":
            changed = source_state != "archived"
            if changed:
                current = archive_pdf(current, requested_by=actor)
        return LifecycleTransitionResult(
            pdf=current,
            source_state=source_state,
            target_state=policy.target_state,
            changed=changed,
            reason_code=policy.reason_code,
            reversible=policy.reversible,
            impact=impact,
        )


def build_recovery_decision(pdf: PDFFile) -> RecoveryDecision:
    """Return the only safe contextual recovery for a hidden document."""
    if pdf.lifecycle in {"deprecated", "archived"}:
        return RecoveryDecision(
            source_state=pdf.lifecycle,
            target_state="uploaded",
            operation="restore_to_uploaded",
            required_evidence=(),
            evidence_bound=True,
        )
    if pdf.lifecycle == "unavailable":
        requirements = LIFECYCLE_POLICIES[
            "source_temporarily_unavailable"
        ].recovery_requirements
        evidence_bound = bool(
            pdf.media_expected_sha256
            and pdf.media_expected_size is not None
            and pdf.media_prior_lifecycle in ALL_LIFECYCLE_STATES - {"unavailable"}
        )
        return RecoveryDecision(
            source_state="unavailable",
            target_state=pdf.media_prior_lifecycle or None,
            operation="verified_media_restore",
            required_evidence=requirements,
            evidence_bound=evidence_bound,
        )
    raise InvalidLifecycleSourceState(
        "recovery_not_applicable",
        f"Lifecycle '{pdf.lifecycle}' does not have a contextual recovery action.",
    )


def recover_document(pdf: PDFFile, *, actor: Any) -> LifecycleTransitionResult:
    """Apply the contextual recovery without bypassing media verification."""
    _require_actor_role(actor)

    current = PDFFile.objects.get(pk=pdf.pk)
    decision = build_recovery_decision(current)
    if decision.operation == "verified_media_restore":
        # Like quarantine, verified media restore owns its transaction and
        # mutation-tracking boundary.
        outcome = restore_unavailable_pdf(current, requested_by=actor)
        return LifecycleTransitionResult(
            pdf=outcome.pdf,
            source_state="unavailable",
            target_state=outcome.pdf.lifecycle,
            changed=outcome.changed,
            reason_code=None,
            reversible=False,
            impact=None,
        )

    with transaction.atomic():
        current = PDFFile.objects.select_for_update().get(pk=pdf.pk)
        decision = build_recovery_decision(current)
        source_state = current.lifecycle
        outcome = restore_pdf(current, requested_by=actor)
        return LifecycleTransitionResult(
            pdf=outcome.pdf,
            source_state=source_state,
            target_state=outcome.pdf.lifecycle,
            changed=outcome.changed,
            reason_code=None,
            reversible=False,
            impact=None,
        )
