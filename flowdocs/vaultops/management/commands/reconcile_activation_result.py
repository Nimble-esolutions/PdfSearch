from django.core.management.base import BaseCommand, CommandError

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
            intent = ActivationIntent.objects.get(
                public_id=options["intent_id"]
            )
            intent = reconcile_activation_result(intent)
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
