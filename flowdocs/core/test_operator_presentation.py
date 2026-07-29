import ast
from pathlib import Path

from django.test import SimpleTestCase
from django.utils import translation

from core.operator_presentation import (
    LABELS,
    REASONS,
    UNKNOWN_REASON,
    UI_REASON_CODES,
    decorate_operator_state,
    label_for,
    present_reason,
)


class OperatorPresentationTests(SimpleTestCase):
    @staticmethod
    def _literal_producer_reasons():
        flowdocs_root = Path(__file__).resolve().parents[1]
        producer_paths = (
            flowdocs_root / "core" / "services" / "dashboard_read_model.py",
            flowdocs_root / "core" / "maintenance_plans.py",
            flowdocs_root / "core" / "candidate_maintenance.py",
            flowdocs_root / "vaultops" / "services" / "read_model.py",
            flowdocs_root / "vaultops" / "views.py",
        )
        reasons = set()
        for path in producer_paths:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                function_name = (
                    node.func.id
                    if isinstance(node.func, ast.Name)
                    else node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else ""
                )
                if function_name in {
                    "WorkbenchRequestError",
                    "MaintenancePlanError",
                } and node.args:
                    value = node.args[0]
                    if isinstance(value, ast.Constant) and isinstance(
                        value.value, str
                    ):
                        reasons.add(value.value)
                for keyword in node.keywords:
                    if keyword.arg != "reason_code":
                        continue
                    value = keyword.value
                    if isinstance(value, ast.Constant) and isinstance(
                        value.value, str
                    ) and value.value:
                        reasons.add(value.value)
        return reasons

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

    def test_worker_and_first_generation_reasons_have_authored_copy(self):
        worker = present_reason("maintenance_worker_unavailable")
        first_generation = present_reason("vault_inventory_setup_required")

        self.assertEqual(
            worker["title"], "Document maintenance is temporarily unavailable"
        )
        self.assertEqual(worker["severity"], "warning")
        self.assertEqual(
            first_generation["title"],
            "Vault storage is ready for its first generation",
        )
        self.assertEqual(first_generation["severity"], "info")

    def test_cold_start_maintenance_reasons_have_bootstrap_guidance(self):
        pointer = present_reason("maintenance_source_pointer_unverified")
        generation = present_reason("maintenance_source_generation_unprojected")
        observation = present_reason("maintenance_source_observation_stale")

        self.assertEqual(pointer["title"], "Active search source is not verified")
        self.assertEqual(
            pointer["action_label"],
            "Restore or activate a verified generation",
        )
        self.assertEqual(
            generation["action_url"],
            "/dashboard/operations/?section=restore",
        )
        self.assertEqual(
            observation["action_url"],
            "/dashboard/operations/?section=jobs",
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
            "options": {"candidate_state": "building"},
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
        self.assertEqual(
            state["options"]["candidate_state_label"],
            "Candidate preparation in progress",
        )

    def test_registry_covers_every_inventoried_ui_reason(self):
        self.assertEqual(UI_REASON_CODES - REASONS.keys(), set())

    def test_literal_ui_producer_reasons_are_in_inventory(self):
        self.assertEqual(
            self._literal_producer_reasons() - UI_REASON_CODES,
            set(),
        )

    def test_every_inventoried_ui_reason_has_complete_authored_copy(self):
        required_fields = {
            "title",
            "detail",
            "consequence",
            "action_label",
            "action_url",
            "severity",
            "technical_code",
            "known",
        }
        for code in sorted(UI_REASON_CODES):
            with self.subTest(code=code):
                presentation = present_reason(code)
                self.assertEqual(set(presentation), required_fields)
                self.assertTrue(presentation["known"])
                self.assertEqual(presentation["technical_code"], code)
                for field in (
                    "title",
                    "detail",
                    "consequence",
                    "action_label",
                    "action_url",
                    "severity",
                ):
                    self.assertTrue(presentation[field])

    def test_success_outcomes_do_not_use_unknown_warning_copy(self):
        success_reasons = {
            "maintenance_plan_created",
            "maintenance_job_queued",
            "maintenance_candidate_prepared",
            "restore_queued",
            "job_retry_queued",
            "profile_configured",
            "profile_probe_completed",
            "profile_inventory_verified",
            "sync_queued",
            "confirmation_issued",
            "promotion_queued",
            "generation_retired",
            "generation_unretired",
            "retention_hold_created",
            "retention_hold_released",
            "gc_plan_created",
            "activation_scheduled",
            "runtime_rollback_scheduled",
        }
        for code in sorted(success_reasons):
            with self.subTest(code=code):
                presentation = present_reason(code)
                self.assertTrue(presentation["known"])
                self.assertNotEqual(
                    presentation["title"], UNKNOWN_REASON["title"]
                )
                self.assertIn(presentation["severity"], {"success", "info"})

    def test_marathi_catalog_resolves_authored_copy(self):
        fields = ("title", "detail", "consequence", "action_label")
        english_reasons = {
            code: present_reason(code) for code in sorted(REASONS)
        }
        english_unknown = present_reason("test_unknown_reason")
        english_labels = {value: label_for(value) for value in LABELS}

        with translation.override("mr"):
            for code, english in english_reasons.items():
                marathi = present_reason(code)
                for field in fields:
                    with self.subTest(code=code, field=field):
                        self.assertNotEqual(marathi[field], english[field])
            marathi_unknown = present_reason("test_unknown_reason")
            for field in fields:
                with self.subTest(code="unknown", field=field):
                    self.assertNotEqual(
                        marathi_unknown[field], english_unknown[field]
                    )
            for value, english in english_labels.items():
                with self.subTest(label=value):
                    self.assertNotEqual(label_for(value), english)
