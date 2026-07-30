from django.conf import settings

from vaultops.models import (
    ActivationIntent,
    ArtifactGeneration,
    RuntimePointerObservation,
)
from vaultops.runtime_control import (
    RuntimeControlError,
    read_runtime_pointer,
    read_signed_document,
    runtime_control_paths,
)
from vaultops.services.runtime_verification import verify_activation_runtime


class RecoveryCertificationError(RuntimeControlError):
    pass


def _fail(reason_code):
    raise RecoveryCertificationError(reason_code)


def verify_recovery_certification(
    *,
    intent_id,
    expected_generation_id,
    expected_manifest_digest,
    require_initial,
):
    """Bind signed runtime evidence to its durable control-plane projections."""
    paths = runtime_control_paths(settings.DATA_CONTROL_ROOT)
    try:
        signed_intent = read_signed_document(
            paths["intents"] / f"{intent_id}.json",
            signing_key=settings.ACTIVATION_INTENT_SIGNING_KEY,
            expected_kind="activation_intent",
            deployment_id=settings.ENV_IDENTITY.deployment_id,
        )
        signed_result = read_signed_document(
            paths["results"] / f"{intent_id}.json",
            signing_key=settings.ACTIVATION_INTENT_SIGNING_KEY,
            expected_kind="activation_result",
            deployment_id=settings.ENV_IDENTITY.deployment_id,
        )
        active = read_runtime_pointer(
            paths["active"],
            deployment_id=settings.ENV_IDENTITY.deployment_id,
            signing_key=settings.ACTIVATION_INTENT_SIGNING_KEY,
            runtime_root=settings.RUNTIME_GENERATIONS_ROOT,
        )
    except RuntimeControlError:
        _fail(
            "recovery_certification_signed_evidence_invalid"
        )
    if (
        signed_intent.get("intent_id") != intent_id
        or signed_intent.get("target_generation_id")
        != expected_generation_id
        or signed_intent.get("target_manifest_digest")
        != expected_manifest_digest
        or signed_result.get("intent_id") != intent_id
        or signed_result.get("intent_digest")
        != signed_intent.get("document_digest")
    ):
        _fail("recovery_certification_identity_mismatch")
    if (
        signed_result.get("status") != "committed"
        or signed_result.get("safe_error_code", "")
        or signed_result.get("active_generation_id")
        != expected_generation_id
        or signed_result.get("active_manifest_digest")
        != expected_manifest_digest
        or active.generation_id != expected_generation_id
        or active.manifest_digest != expected_manifest_digest
        or signed_result.get("active_pointer_digest")
        != active.pointer_digest
    ):
        _fail("recovery_certification_result_uncommitted")
    readiness = signed_result.get("readiness_evidence")
    if not isinstance(readiness, dict) or (
        readiness.get("livez") != "ok"
        or readiness.get("readyz") != "ready"
        or readiness.get("runtime_generation_id")
        != expected_generation_id
        or readiness.get("runtime_manifest_digest")
        != expected_manifest_digest
        or readiness.get("runtime_smoke") != "passed"
        or readiness.get("unavailable_documents")
        != signed_intent.get("unavailable_documents")
    ):
        _fail("recovery_certification_readiness_mismatch")
    try:
        intent = ActivationIntent.objects.using("control").get(
            public_id=intent_id,
            deployment_id=settings.ENV_IDENTITY.deployment_id,
        )
    except ActivationIntent.DoesNotExist:
        _fail("recovery_certification_intent_unprojected")
    if (
        intent.target_generation_id != expected_generation_id
        or intent.manifest_digest != expected_manifest_digest
        or intent.intent_digest != signed_intent.get("document_digest")
        or intent.state != ActivationIntent.State.COMMITTED
        or intent.safe_error_code
        or intent.applied_at is None
        or intent.committed_at is None
        or intent.checkpoint.get("protocol_state") != "committed"
        or intent.checkpoint.get("active_pointer_digest")
        != active.pointer_digest
    ):
        _fail("recovery_certification_intent_projection_mismatch")
    generation = (
        ArtifactGeneration.objects.using("control")
        .filter(
            deployment_id=settings.ENV_IDENTITY.deployment_id,
            generation_id=expected_generation_id,
            manifest_digest=expected_manifest_digest,
        )
        .order_by("-updated_at")
        .first()
    )
    if generation is None:
        _fail("recovery_certification_generation_unprojected")
    if generation.runtime_state != ArtifactGeneration.RuntimeState.ACTIVE:
        _fail("recovery_certification_generation_not_active")
    observation = (
        RuntimePointerObservation.objects.using("control")
        .filter(
            deployment_id=settings.ENV_IDENTITY.deployment_id,
            active_generation_id=expected_generation_id,
            pointer_digest=active.pointer_digest,
            status="committed",
            observed_at__gte=intent.applied_at,
        )
        .order_by("-observed_at")
        .first()
    )
    if (
        observation is None
        or observation.previous_generation_id
        != signed_result.get("previous_generation_id", "")
        or observation.process_identity
        != signed_result.get("process_identity", {})
        or observation.readiness_evidence != readiness
    ):
        _fail("recovery_certification_observation_mismatch")
    if require_initial and (
        signed_intent.get("activation_mode") != "initial"
        or signed_intent.get("previous_generation_id", "")
        or signed_intent.get("previous_pointer_digest", "")
        or signed_result.get("previous_generation_id", "")
        or readiness.get("initial_activation") is not True
        or intent.previous_generation_id
        or intent.checkpoint.get("initial_activation") is not True
        or paths["previous"].exists()
    ):
        _fail("recovery_certification_initial_authority_invalid")
    runtime_evidence = verify_activation_runtime(intent_id)
    if runtime_evidence.get("executed_locales") != ["en", "mr"]:
        _fail("recovery_certification_bilingual_smoke_incomplete")
    return {
        "schema_version": 1,
        "status": "passed",
        "intent_id": intent_id,
        "intent_digest": intent.intent_digest,
        "generation_id": expected_generation_id,
        "manifest_digest": expected_manifest_digest,
        "pointer_digest": active.pointer_digest,
        "activation_result": "committed",
        "control_projection": "committed",
        "initial_authority": "absent" if require_initial else "not_required",
        "readiness": {
            "livez": "ok",
            "readyz": "ready",
            "runtime_generation_id": expected_generation_id,
            "runtime_manifest_digest": expected_manifest_digest,
        },
        "runtime": runtime_evidence,
    }
