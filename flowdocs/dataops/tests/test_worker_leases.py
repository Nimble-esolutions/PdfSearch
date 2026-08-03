from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from dataops.models import DataOperation, DataOpsAuditEvent
from dataops.worker import (
    _claim_pipeline_operation,
    _finish_pipeline_operation,
    reconcile_receipts,
)


class DataOperationLeaseTests(TestCase):
    databases = {"default", "control"}

    def _operation(self, *, state=DataOperation.State.QUEUED, stage="preflight"):
        return DataOperation.objects.using("control").create(
            kind=DataOperation.Kind.BACKUP,
            state=state,
            pipeline_stage=stage,
        )

    def test_lease_prevents_duplicate_claim_until_expiry(self):
        operation = self._operation()

        first = _claim_pipeline_operation(operation.pk)
        self.assertIsNotNone(first)
        claimed, first_token = first
        self.assertEqual(claimed.lease_token, first_token)
        self.assertIsNone(_claim_pipeline_operation(operation.pk))

        DataOperation.objects.using("control").filter(pk=operation.pk).update(
            lease_expires_at=timezone.now() - timedelta(seconds=1),
            pipeline_stage="transfer",
        )
        recovered = _claim_pipeline_operation(operation.pk)
        self.assertIsNotNone(recovered)
        _, second_token = recovered
        self.assertNotEqual(first_token, second_token)

    def test_reconciler_executes_only_the_leases_it_claims(self):
        first = self._operation()
        second = self._operation()

        with patch("dataops.worker._finish_pipeline_operation") as finish:
            self.assertEqual(reconcile_receipts(limit=1), 1)
            self.assertEqual(finish.call_count, 1)

        first.refresh_from_db(using="control")
        second.refresh_from_db(using="control")
        self.assertEqual(first.state, DataOperation.State.RUNNING)
        self.assertEqual(second.state, DataOperation.State.QUEUED)
        self.assertTrue(first.lease_token)
        self.assertIsNotNone(first.lease_expires_at)

    def test_success_with_null_activation_persists_one_audit_event(self):
        operation = self._operation()
        operation.lifecycle_plan = {"contract_version": 3}
        operation.save(using="control", update_fields=["lifecycle_plan", "updated_at"])
        result = {
            "release_id": "release-1",
            "manifest_digest": "abc123",
            "source_profile": "source",
            "destination_profile": "destination",
            "activation": None,
        }

        with patch("dataops.v3_executor.execute_backup_operation", return_value=result):
            finished = _finish_pipeline_operation(operation)

        operation.refresh_from_db(using="control")
        self.assertEqual(finished.state, DataOperation.State.SUCCEEDED)
        self.assertEqual(operation.state, DataOperation.State.SUCCEEDED)
        events = DataOpsAuditEvent.objects.using("control").filter(
            operation_id=operation.public_id,
            action="pipeline_receipt_published",
            outcome=DataOperation.State.SUCCEEDED,
        )
        self.assertEqual(events.count(), 1)
        self.assertEqual(events.get().evidence["active_generation"], "")

    def test_success_audit_failure_rolls_back_success_state(self):
        operation = self._operation()
        operation.lifecycle_plan = {"contract_version": 3}
        operation.save(using="control", update_fields=["lifecycle_plan", "updated_at"])
        result = {
            "release_id": "release-1",
            "manifest_digest": "abc123",
            "activation": None,
        }

        with (
            patch("dataops.v3_executor.execute_backup_operation", return_value=result),
            patch(
                "dataops.models.DataOpsAuditEvent.save",
                side_effect=RuntimeError("audit unavailable"),
            ),
            self.assertRaisesRegex(RuntimeError, "audit unavailable"),
        ):
            _finish_pipeline_operation(operation)

        operation.refresh_from_db(using="control")
        self.assertEqual(operation.state, DataOperation.State.RUNNING)
        self.assertEqual(operation.pipeline_stage, "preflight")
        self.assertFalse(
            DataOpsAuditEvent.objects.using("control")
            .filter(operation_id=operation.public_id, action="pipeline_receipt_published")
            .exists()
        )
