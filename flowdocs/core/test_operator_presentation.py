from django.test import SimpleTestCase
from django.utils import translation

from core.operator_presentation import (
    REASONS,
    decorate_operator_state,
    label_for,
    present_reason,
)


class OperatorPresentationTests(SimpleTestCase):
    def test_known_reason_has_complete_operator_copy(self):
        presentation = present_reason("external_embeddings_disabled")
        self.assertEqual(presentation["title"], "Embedding service is unavailable")
        self.assertEqual(presentation["severity"], "warning")
        self.assertTrue(presentation["detail"])
        self.assertTrue(presentation["consequence"])
        self.assertTrue(presentation["action_label"])
        self.assertEqual(
            presentation["technical_code"], "external_embeddings_disabled"
        )

    def test_unknown_reason_never_infers_copy_from_token(self):
        presentation = present_reason("future_unknown_machine_token")
        self.assertFalse(presentation["known"])
        self.assertEqual(
            presentation["title"],
            "Additional technical evidence requires review.",
        )
        self.assertNotIn("future unknown machine token", presentation["title"])
        self.assertEqual(
            presentation["technical_code"], "future_unknown_machine_token"
        )

    def test_decorator_is_additive_and_preserves_stable_values(self):
        state = {
            "status": "retryable_failed",
            "reason_code": "worker_heartbeat_expired",
            "safe_error_code": "restore_failed",
        }
        decorate_operator_state(state)
        self.assertEqual(state["status"], "retryable_failed")
        self.assertEqual(state["reason_code"], "worker_heartbeat_expired")
        self.assertEqual(state["safe_error_code"], "restore_failed")
        self.assertEqual(state["status_label"], "Retry available")
        self.assertEqual(
            state["presentation"]["technical_code"],
            "worker_heartbeat_expired",
        )
        self.assertEqual(
            state["error_presentation"]["technical_code"], "restore_failed"
        )

    def test_registry_covers_dashboard_and_workbench_boundary_reasons(self):
        required = {
            "operations_ready",
            "maintenance_job_failed",
            "index_debt_present",
            "provenance_review_required",
            "maintenance_in_progress",
            "empty_categories_present",
            "external_embeddings_disabled",
            "capacity_degraded",
            "generation_authoritative",
            "generation_runtime_referenced",
            "workspace_referenced",
            "generation_job_in_progress",
            "retention_hold_active",
            "rollback_pointer_missing",
            "rollback_pointer_unverified",
            "rollback_lineage_invalid",
        }
        self.assertEqual(required - REASONS.keys(), set())

    def test_marathi_catalog_resolves_authored_copy(self):
        with translation.override("mr"):
            self.assertNotEqual(
                present_reason("external_embeddings_disabled")["title"],
                "Embedding service is unavailable",
            )
            self.assertNotEqual(label_for("retryable_failed"), "Retry available")
