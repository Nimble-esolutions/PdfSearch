from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from dataops.models import DataOpsAuditEvent, RestoreCandidate
from vaultops.models import ActivationIntent
from vaultops.services.activation import (
    ActivationCoordinatorError,
    reconcile_activation_result,
)


class Command(BaseCommand):
    help = "Project one signed supervisor activation result"

    def add_arguments(self, parser):
        parser.add_argument("--intent-id", required=True)

    def handle(self, *args, **options):
        try:
            intent = ActivationIntent.objects.using("control").get(
                public_id=options["intent_id"]
            )
            intent = reconcile_activation_result(intent)
            self._project_dataops_result(intent)
        except (
            ActivationIntent.DoesNotExist,
            ActivationCoordinatorError,
        ) as exc:
            reason_code = getattr(
                exc, "reason_code", "activation_intent_not_found"
            )
            raise CommandError(reason_code) from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"Activation intent {intent.public_id}: {intent.state}"
            )
        )

    def _project_dataops_result(self, intent):
        candidates = RestoreCandidate.objects.using("control").filter(
            state__in=[
                RestoreCandidate.State.READY,
                RestoreCandidate.State.ACTIVATED,
            ]
        ).order_by("-updated_at")[:1000]
        candidate = next(
            (
                item
                for item in candidates
                if str(
                    (item.evidence or {})
                    .get("activation", {})
                    .get("intent_id")
                    or ""
                )
                == str(intent.public_id)
            ),
            None,
        )
        if candidate is None:
            return
        activation = dict((candidate.evidence or {}).get("activation") or {})
        activation.update(
            {
                "state": intent.state,
                "safe_error_code": intent.safe_error_code,
                "committed_at": (
                    intent.committed_at.isoformat() if intent.committed_at else None
                ),
            }
        )
        candidate.evidence = {
            **(candidate.evidence or {}),
            "activation": activation,
        }
        update_fields = ["evidence", "updated_at"]
        if intent.state == ActivationIntent.State.COMMITTED:
            candidate.state = RestoreCandidate.State.ACTIVATED
            candidate.activated_at = intent.committed_at or timezone.now()
            update_fields.extend(["state", "activated_at"])
            candidate.recovery_point.activation_ready = True
            candidate.recovery_point.save(
                update_fields=["activation_ready", "updated_at"]
            )
        candidate.save(update_fields=update_fields)
        DataOpsAuditEvent.objects.using("control").create(
            action="activation_result_reconciled",
            operation_id=candidate.operation.public_id,
            outcome=(
                "succeeded"
                if intent.state == ActivationIntent.State.COMMITTED
                else "failed"
            ),
            evidence={
                "candidate_id": candidate.pk,
                "intent_id": str(intent.public_id),
                "intent_digest": intent.intent_digest,
                "generation_id": intent.target_generation_id,
                "manifest_digest": intent.manifest_digest,
                "state": intent.state,
            },
        )
