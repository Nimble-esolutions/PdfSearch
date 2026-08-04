"""Focused contract tests for the outcome-based document lifecycle service."""

from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from .models import Folder, PDFFile
from .services.document_lifecycle import (
    ALL_LIFECYCLE_STATES,
    InvalidLifecycleSourceState,
    LIFECYCLE_POLICIES,
    LifecycleInputRejected,
    LifecyclePermissionDenied,
    UnknownLifecycleReason,
    actor_can_manage_lifecycle,
    build_impact_preview,
    build_recovery_decision,
    get_lifecycle_policy,
    recover_document,
    remove_from_search,
)
from .utils import SearchDataIntegrityError


class DocumentLifecyclePolicyTests(SimpleTestCase):
    def test_reason_codes_map_to_existing_lifecycle_states(self):
        self.assertEqual(get_lifecycle_policy("superseded").target_state, "deprecated")
        self.assertEqual(
            get_lifecycle_policy("historical_record").target_state,
            "archived",
        )
        self.assertEqual(
            get_lifecycle_policy("source_temporarily_unavailable").target_state,
            "unavailable",
        )

    def test_policies_define_allowed_states_reversibility_and_inputs(self):
        superseded = LIFECYCLE_POLICIES["superseded"]
        historical = LIFECYCLE_POLICIES["historical_record"]
        unavailable = LIFECYCLE_POLICIES["source_temporarily_unavailable"]

        self.assertTrue(superseded.reversible)
        self.assertEqual(superseded.required_inputs, ())
        self.assertNotIn("archived", superseded.allowed_source_states)
        self.assertTrue(historical.reversible)
        self.assertIn("deprecated", historical.allowed_source_states)
        self.assertEqual(unavailable.allowed_source_states, ALL_LIFECYCLE_STATES)
        self.assertEqual(
            unavailable.required_inputs,
            ("quarantine_reason", "case_reference"),
        )
        self.assertIn("matching_local_media", unavailable.recovery_requirements)

    def test_unknown_and_permanent_delete_reasons_are_rejected(self):
        for reason_code in ("", "not_a_reason", "permanent_delete"):
            with self.subTest(reason_code=reason_code):
                with self.assertRaises(UnknownLifecycleReason):
                    get_lifecycle_policy(reason_code)

    def test_actor_role_check_matches_application_admin_roles(self):
        self.assertTrue(
            actor_can_manage_lifecycle(
                SimpleNamespace(is_authenticated=True, role="admin")
            )
        )
        self.assertTrue(
            actor_can_manage_lifecycle(
                SimpleNamespace(is_authenticated=True, role="superadmin")
            )
        )
        self.assertFalse(
            actor_can_manage_lifecycle(
                SimpleNamespace(is_authenticated=True, role="viewer")
            )
        )
        self.assertFalse(
            actor_can_manage_lifecycle(
                SimpleNamespace(is_authenticated=False, role="admin")
            )
        )


class DocumentLifecycleServiceTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(
            username="lifecycle-service-admin",
            password="test-password",
            role="admin",
        )
        self.folder = Folder.objects.create(
            name="Lifecycle service folder",
            created_by=self.admin,
        )
        self.pdf = PDFFile.objects.create(
            title="Lifecycle service PDF",
            file="pdfs/lifecycle-service.pdf",
            folder=self.folder,
            uploaded_by=self.admin,
            lifecycle="ready",
            indexed=True,
        )

    def _snapshot(self):
        self.pdf.refresh_from_db()
        return {
            "lifecycle": self.pdf.lifecycle,
            "indexed": self.pdf.indexed,
            "media_prior_lifecycle": self.pdf.media_prior_lifecycle,
            "media_expected_sha256": self.pdf.media_expected_sha256,
            "media_expected_size": self.pdf.media_expected_size,
            "media_quarantine_reason": self.pdf.media_quarantine_reason,
            "media_case_reference": self.pdf.media_case_reference,
            "file_name": self.pdf.file.name,
            "folder_id": self.pdf.folder_id,
        }

    def test_superseded_uses_deprecated_contract(self):
        result = remove_from_search(
            self.pdf,
            reason_code="superseded",
            actor=self.admin,
        )

        self.pdf.refresh_from_db()
        self.assertTrue(result.changed)
        self.assertEqual(result.source_state, "ready")
        self.assertEqual(result.target_state, "deprecated")
        self.assertEqual(self.pdf.lifecycle, "deprecated")
        self.assertFalse(self.pdf.indexed)
        self.assertEqual(self.pdf.file.name, "pdfs/lifecycle-service.pdf")

    def test_historical_record_uses_archived_contract(self):
        result = remove_from_search(
            self.pdf,
            reason_code="historical_record",
            actor=self.admin,
        )

        self.pdf.refresh_from_db()
        self.assertTrue(result.changed)
        self.assertEqual(self.pdf.lifecycle, "archived")
        self.assertFalse(self.pdf.indexed)

    def test_invalid_reason_rejects_without_mutation(self):
        before = self._snapshot()

        with self.assertRaises(UnknownLifecycleReason):
            remove_from_search(
                self.pdf,
                reason_code="delete_forever",
                actor=self.admin,
            )

        self.assertEqual(self._snapshot(), before)

    def test_invalid_source_state_rejects_without_mutation(self):
        self.pdf.lifecycle = "archived"
        self.pdf.indexed = False
        self.pdf.save(update_fields=["lifecycle", "indexed"])
        before = self._snapshot()

        with self.assertRaises(InvalidLifecycleSourceState):
            remove_from_search(
                self.pdf,
                reason_code="superseded",
                actor=self.admin,
            )

        self.assertEqual(self._snapshot(), before)

    def test_non_admin_role_rejects_without_mutation(self):
        before = self._snapshot()
        viewer = SimpleNamespace(is_authenticated=True, role="viewer")

        with self.assertRaises(LifecyclePermissionDenied):
            remove_from_search(
                self.pdf,
                reason_code="historical_record",
                actor=viewer,
            )

        self.assertEqual(self._snapshot(), before)

    def test_impact_preview_is_safe_and_complete(self):
        before = self._snapshot()

        preview = build_impact_preview(self.pdf, "superseded")

        self.assertEqual(preview.document_id, self.pdf.pk)
        self.assertEqual(preview.file_action, "preserve")
        self.assertTrue(preview.file_reference_present)
        self.assertTrue(preview.index_marked_before)
        self.assertFalse(preview.index_marked_after)
        self.assertTrue(preview.folder_index_rebuild_required)
        self.assertTrue(preview.search_visible_before)
        self.assertFalse(preview.search_visible_after)
        self.assertEqual(preview.folder_id, self.folder.pk)
        self.assertEqual(preview.folder_name, self.folder.name)
        self.assertEqual(preview.folder_membership_action, "preserve")
        self.assertFalse(preview.permanent_deletion)
        self.assertEqual(preview.as_dict()["file"]["action"], "preserve")
        self.assertEqual(self._snapshot(), before)

    def test_unavailable_requires_operator_evidence_before_mutation(self):
        before = self._snapshot()

        with self.assertRaises(LifecycleInputRejected):
            remove_from_search(
                self.pdf,
                reason_code="source_temporarily_unavailable",
                actor=self.admin,
                evidence={"case_reference": "CASE-123"},
            )

        self.assertEqual(self._snapshot(), before)

    def test_unavailable_rejects_unapproved_evidence_reason_without_mutation(self):
        before = self._snapshot()

        with self.assertRaises(LifecycleInputRejected):
            remove_from_search(
                self.pdf,
                reason_code="source_temporarily_unavailable",
                actor=self.admin,
                evidence={
                    "quarantine_reason": "operator_guess",
                    "case_reference": "CASE-123",
                },
            )

        self.assertEqual(self._snapshot(), before)

    def test_unavailable_preserves_existing_absent_media_contract(self):
        result = remove_from_search(
            self.pdf,
            reason_code="source_temporarily_unavailable",
            actor=self.admin,
            evidence={
                "quarantine_reason": "missing_after_inventory",
                "case_reference": "CASE-123",
                "expected_sha256": "a" * 64,
                "expected_size": 128,
            },
        )

        self.pdf.refresh_from_db()
        self.assertTrue(result.changed)
        self.assertEqual(self.pdf.lifecycle, "unavailable")
        self.assertEqual(self.pdf.media_prior_lifecycle, "ready")
        self.assertEqual(self.pdf.media_expected_sha256, "a" * 64)
        self.assertEqual(self.pdf.media_expected_size, 128)
        self.assertEqual(self.pdf.media_case_reference, "CASE-123")

    def test_unavailable_recovery_does_not_bypass_exact_evidence(self):
        remove_from_search(
            self.pdf,
            reason_code="source_temporarily_unavailable",
            actor=self.admin,
            evidence={
                "quarantine_reason": "missing_after_inventory",
                "case_reference": "CASE-UNKNOWN-BYTES",
            },
        )
        self.pdf.refresh_from_db()
        decision = build_recovery_decision(self.pdf)
        self.assertFalse(decision.evidence_bound)
        before = self._snapshot()

        with self.assertRaisesRegex(
            SearchDataIntegrityError,
            "does not have durable restoration evidence",
        ):
            recover_document(self.pdf, actor=self.admin)

        self.assertEqual(self._snapshot(), before)

    def test_contextual_recovery_of_deprecated_document_is_explicit(self):
        remove_from_search(
            self.pdf,
            reason_code="superseded",
            actor=self.admin,
        )
        self.pdf.refresh_from_db()
        decision = build_recovery_decision(self.pdf)
        self.assertEqual(decision.operation, "restore_to_uploaded")
        self.assertEqual(decision.target_state, "uploaded")

        result = recover_document(self.pdf, actor=self.admin)

        self.pdf.refresh_from_db()
        self.assertTrue(result.changed)
        self.assertEqual(self.pdf.lifecycle, "uploaded")
        self.assertFalse(self.pdf.indexed)

    def test_recovery_is_rejected_for_visible_document_without_mutation(self):
        before = self._snapshot()

        with self.assertRaises(InvalidLifecycleSourceState):
            recover_document(self.pdf, actor=self.admin)

        self.assertEqual(self._snapshot(), before)
