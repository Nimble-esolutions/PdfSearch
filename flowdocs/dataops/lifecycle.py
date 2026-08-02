"""Pure planning contract for the Data Operations v3 lifecycle.

The operator describes an intent.  This module decides the safe route from
instance identity and artifact provenance; it never resolves credentials,
touches storage, or mutates the active runtime.  Executors must persist and
bind the resulting digest to their receipts and activation evidence.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


CONTRACT_VERSION = 3


class LifecycleIntent(str, Enum):
    IMPORT = "import"
    BACKUP = "backup"
    RESTORE = "restore"
    TEST_RECOVERY = "test_recovery"
    ROLLBACK = "rollback"


class LifecycleRoute(str, Enum):
    BACKUP = "backup"
    LEGACY_IMPORT = "legacy_import"
    SAME_DATASET_RESTORE = "same_dataset_restore"
    IMPORT_REBIND_RESTORE = "import_rebind_restore"
    ISOLATED_REHEARSAL = "isolated_rehearsal"
    ROLLBACK = "rollback"
    NOOP = "noop"
    REFUSE = "refuse"


class SourceKind(str, Enum):
    ACTIVE_INSTANCE = "active_instance"
    LEGACY_MOUNT = "legacy_mount"
    LEGACY_OBJECT_STORE = "legacy_object_store"
    RECOVERY_POINT = "recovery_point"
    PREVIOUS_RUNTIME = "previous_runtime"


class ArtifactTrust(str, Enum):
    UNKNOWN = "unknown"
    PROVISIONAL = "provisional"
    VERIFIED = "verified"


@dataclass(frozen=True)
class InstanceIdentity:
    environment: str
    deployment_id: str
    dataset_id: str
    active_generation_id: str = ""
    active_manifest_sha256: str = ""

    @property
    def normalized_environment(self) -> str:
        value = self.environment.strip().lower()
        aliases = {
            "dev": "development",
            "stage": "staging",
            "prod": "production",
        }
        return aliases.get(value, value)


@dataclass(frozen=True)
class ArtifactPassport:
    source_kind: SourceKind
    dataset_id: str = ""
    generation_id: str = ""
    manifest_sha256: str = ""
    format_version: int = 0
    trust: ArtifactTrust = ArtifactTrust.UNKNOWN
    complete: bool = False
    read_only: bool = True
    signature_valid: bool | None = None
    producer_release: str = ""
    producer_image_digest: str = ""
    database_schema: str = ""
    parent_dataset_id: str = ""
    parent_generation_id: str = ""
    parent_manifest_sha256: str = ""

    @property
    def is_legacy(self) -> bool:
        return self.source_kind in {
            SourceKind.LEGACY_MOUNT,
            SourceKind.LEGACY_OBJECT_STORE,
        }

    @property
    def is_verified(self) -> bool:
        return self.trust is ArtifactTrust.VERIFIED and self.complete


@dataclass(frozen=True)
class LifecycleCapabilities:
    owned_store_readable: bool = False
    owned_store_writable: bool = False
    source_readable: bool = False
    quarantine_writable: bool = False
    signing_available: bool = False
    activation_available: bool = False
    isolated_restore_available: bool = False
    previous_runtime_available: bool = False


@dataclass(frozen=True)
class LifecycleRequest:
    intent: LifecycleIntent
    activate: bool = False
    confirmation_present: bool = False
    previous_recovery_point_exists: bool = False


@dataclass(frozen=True)
class LifecyclePlan:
    contract_version: int
    intent: str
    route: str
    environment: str
    deployment_id: str
    target_dataset_id: str
    source_dataset_id: str
    source_generation_id: str
    source_manifest_sha256: str
    steps: tuple[str, ...]
    gates: tuple[str, ...]
    reason_codes: tuple[str, ...]
    refusal_codes: tuple[str, ...]
    activation: str
    transfer_mode: str
    plan_digest: str = ""

    @property
    def allowed(self) -> bool:
        return self.route != LifecycleRoute.REFUSE.value

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _build_plan(
    *,
    request: LifecycleRequest,
    identity: InstanceIdentity,
    passport: ArtifactPassport | None,
    route: LifecycleRoute,
    steps: list[str],
    gates: list[str],
    reasons: list[str],
    refusals: list[str],
    activation: str = "none",
    transfer_mode: str = "none",
) -> LifecyclePlan:
    payload = {
        "contract_version": CONTRACT_VERSION,
        "intent": request.intent.value,
        "route": (LifecycleRoute.REFUSE if refusals else route).value,
        "environment": identity.normalized_environment,
        "deployment_id": identity.deployment_id.strip(),
        "target_dataset_id": identity.dataset_id.strip(),
        "source_dataset_id": passport.dataset_id.strip() if passport else "",
        "source_generation_id": passport.generation_id.strip() if passport else "",
        "source_manifest_sha256": passport.manifest_sha256.strip() if passport else "",
        "steps": tuple(steps),
        "gates": tuple(gates),
        "reason_codes": tuple(dict.fromkeys(reasons)),
        "refusal_codes": tuple(dict.fromkeys(refusals)),
        "activation": activation,
        "transfer_mode": transfer_mode,
    }
    return LifecyclePlan(**payload, plan_digest=_digest(payload))


def _identity_refusals(identity: InstanceIdentity) -> list[str]:
    refusals = []
    if not identity.deployment_id.strip():
        refusals.append("deployment_identity_missing")
    if not identity.dataset_id.strip():
        refusals.append("target_dataset_missing")
    if identity.normalized_environment not in {
        "development",
        "staging",
        "production",
        "review",
        "test",
    }:
        refusals.append("environment_unknown")
    return refusals


def _source_refusals(passport: ArtifactPassport | None) -> list[str]:
    if passport is None:
        return ["source_required"]
    refusals = []
    if not passport.read_only:
        refusals.append("source_must_be_read_only")
    if passport.source_kind in {
        SourceKind.RECOVERY_POINT,
        SourceKind.PREVIOUS_RUNTIME,
    }:
        if not passport.generation_id.strip():
            refusals.append("source_generation_missing")
        if not passport.is_verified:
            refusals.append("source_not_verified")
        if passport.signature_valid is False:
            refusals.append("manifest_signature_invalid")
        if passport.format_version >= CONTRACT_VERSION and passport.signature_valid is not True:
            refusals.append("manifest_signature_missing")
    return refusals


def compile_lifecycle_plan(
    request: LifecycleRequest,
    identity: InstanceIdentity,
    capabilities: LifecycleCapabilities,
    passport: ArtifactPassport | None = None,
) -> LifecyclePlan:
    """Compile one deterministic, secret-free DataOps lifecycle plan.

    Environment controls the strength of activation gates, not whether backup
    and restore exist.  Dataset ownership decides same-dataset restore versus
    automatic import/rebind.
    """

    refusals = _identity_refusals(identity)
    reasons: list[str] = []
    gates: list[str] = []
    steps: list[str] = []
    activation = "none"
    transfer_mode = "none"

    if request.intent is LifecycleIntent.BACKUP:
        if not capabilities.owned_store_writable:
            refusals.append("owned_store_not_writable")
        steps.extend(
            [
                "create_consistent_snapshot",
                "verify_database_and_inventory",
                "publish_content_addressed_objects",
                "publish_immutable_manifest",
                "verify_remote_recovery_point",
                "record_backup_receipt",
            ]
        )
        gates.extend(["stable_snapshot", "sqlite_integrity", "object_hashes", "conditional_registration"])
        if request.previous_recovery_point_exists:
            reasons.append("prior_recovery_point_available")
            transfer_mode = "incremental_deduplicated"
        else:
            reasons.append("first_recovery_point")
            transfer_mode = "full_upload"
        reasons.append("logically_full_manifest")
        return _build_plan(
            request=request,
            identity=identity,
            passport=None,
            route=LifecycleRoute.BACKUP,
            steps=steps,
            gates=gates,
            reasons=reasons,
            refusals=refusals,
            transfer_mode=transfer_mode,
        )

    refusals.extend(_source_refusals(passport))
    if passport is not None and not capabilities.source_readable:
        refusals.append("source_not_readable")

    if request.intent is LifecycleIntent.TEST_RECOVERY:
        if not capabilities.isolated_restore_available:
            refusals.append("isolated_restore_unavailable")
        steps.extend(
            [
                "copy_to_isolated_quarantine",
                "verify_manifest_and_objects",
                "rehearse_migrations",
                "reconcile_media_and_indexes",
                "run_recovery_checks",
                "record_rehearsal_receipt",
            ]
        )
        gates.extend(["manifest_integrity", "sqlite_integrity", "migration_compatibility", "indexing_complete", "functional_checks"])
        reasons.extend(["isolated_recovery_test", "activation_forbidden"])
        return _build_plan(
            request=request,
            identity=identity,
            passport=passport,
            route=LifecycleRoute.ISOLATED_REHEARSAL,
            steps=steps,
            gates=gates,
            reasons=reasons,
            refusals=refusals,
        )

    if request.intent is LifecycleIntent.ROLLBACK:
        if not capabilities.previous_runtime_available:
            refusals.append("previous_runtime_unavailable")
        if not capabilities.activation_available:
            refusals.append("activation_unavailable")
        if not capabilities.signing_available:
            refusals.append("activation_signing_unavailable")
        if not request.confirmation_present:
            refusals.append("operator_confirmation_required")
        steps.extend(["verify_previous_runtime_pair", "sign_rollback_intent", "compare_and_swap_runtime_pointer", "verify_runtime_readiness"])
        gates.extend(["image_generation_compatibility", "expected_active_pointer", "signed_intent", "rollback_readiness"])
        reasons.append("rollback_requested")
        return _build_plan(
            request=request,
            identity=identity,
            passport=passport,
            route=LifecycleRoute.ROLLBACK,
            steps=steps,
            gates=gates,
            reasons=reasons,
            refusals=refusals,
            activation="signed_atomic",
        )

    if passport is None:
        return _build_plan(
            request=request,
            identity=identity,
            passport=None,
            route=LifecycleRoute.REFUSE,
            steps=steps,
            gates=gates,
            reasons=reasons,
            refusals=refusals,
        )

    if passport.is_legacy and not passport.is_verified:
        route = LifecycleRoute.LEGACY_IMPORT
        steps.extend(
            [
                "discover_legacy_layout",
                "scan_source_twice",
                "verify_database_and_inventory",
                "publish_canonical_recovery_point",
                "verify_remote_recovery_point",
            ]
        )
        gates.extend(["read_only_source", "stable_two_scan_snapshot", "sqlite_integrity", "object_hashes", "conditional_registration"])
        reasons.extend(["legacy_source", "canonicalization_required"])
        if not capabilities.owned_store_writable:
            refusals.append("owned_store_not_writable")
    elif passport.dataset_id.strip() == identity.dataset_id.strip():
        if passport.generation_id.strip() == identity.active_generation_id.strip() and passport.manifest_sha256.strip() == identity.active_manifest_sha256.strip():
            route = LifecycleRoute.NOOP
            reasons.extend(["same_dataset", "generation_already_active"])
        else:
            route = LifecycleRoute.SAME_DATASET_RESTORE
            reasons.append("same_dataset")
    else:
        route = LifecycleRoute.IMPORT_REBIND_RESTORE
        reasons.extend(["foreign_dataset", "automatic_clone_rebind"])
        steps.extend(["verify_source_generation", "copy_missing_content_objects", "rewrite_dataset_bindings", "publish_target_owned_manifest"])
        gates.extend(["source_immutable", "destination_collision_free", "lineage_preserved", "destination_object_hashes"])
        if not capabilities.owned_store_writable:
            refusals.append("owned_store_not_writable")

    if route is not LifecycleRoute.NOOP:
        if not capabilities.quarantine_writable:
            refusals.append("quarantine_not_writable")
        steps.extend(
            [
                "restore_to_new_quarantine_generation",
                "verify_manifest_and_objects",
                "rehearse_migrations",
                "reconcile_media_and_indexes",
                "run_recovery_checks",
            ]
        )
        gates.extend(["manifest_integrity", "sqlite_integrity", "migration_compatibility", "indexing_complete", "functional_checks"])

    if request.activate and route is not LifecycleRoute.NOOP:
        activation = "signed_atomic"
        if not capabilities.activation_available:
            refusals.append("activation_unavailable")
        if not capabilities.signing_available:
            refusals.append("activation_signing_unavailable")
        if not request.confirmation_present:
            refusals.append("operator_confirmation_required")
        if identity.normalized_environment == "production":
            steps.insert(0, "publish_pre_restore_recovery_point")
            steps.extend(["enter_mutation_barrier", "sign_activation_intent", "compare_and_swap_runtime_pointer", "verify_runtime_readiness", "leave_mutation_barrier"])
            gates.extend(["pre_restore_backup", "maintenance_window", "rollback_authority", "expected_active_pointer", "signed_intent"])
            reasons.append("production_activation_controls")
        else:
            steps.extend(["sign_activation_intent", "compare_and_swap_runtime_pointer", "verify_runtime_readiness"])
            gates.extend(["expected_active_pointer", "signed_intent"])
        if not capabilities.owned_store_readable:
            refusals.append("owned_store_not_readable")
    elif request.activate and route is LifecycleRoute.NOOP:
        reasons.append("activation_not_required")
    else:
        reasons.append("activation_not_requested")

    return _build_plan(
        request=request,
        identity=identity,
        passport=passport,
        route=route,
        steps=steps,
        gates=gates,
        reasons=reasons,
        refusals=refusals,
        activation=activation,
    )
