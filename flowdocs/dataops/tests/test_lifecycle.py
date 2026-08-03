"""Decision-table tests for the Data Operations v3 lifecycle planner."""

from __future__ import annotations

from dataclasses import replace
import unittest

from dataops.lifecycle import (
    ArtifactPassport,
    ArtifactTrust,
    InstanceIdentity,
    LifecycleCapabilities,
    LifecycleIntent,
    LifecycleRequest,
    LifecycleRoute,
    SourceKind,
    compile_lifecycle_plan,
)


class LifecyclePlannerTests(unittest.TestCase):
    def setUp(self):
        self.stage = InstanceIdentity(
            environment="stage",
            deployment_id="stage-2026",
            dataset_id="ai-sahakar-stage-2026",
            active_generation_id="stage-active",
            active_manifest_sha256="a" * 64,
        )
        self.capabilities = LifecycleCapabilities(
            owned_store_readable=True,
            owned_store_writable=True,
            source_readable=True,
            quarantine_writable=True,
            signing_available=True,
            activation_available=True,
            isolated_restore_available=True,
            previous_runtime_available=True,
        )
        self.stage_point = ArtifactPassport(
            source_kind=SourceKind.RECOVERY_POINT,
            dataset_id=self.stage.dataset_id,
            generation_id="stage-backup-1",
            manifest_sha256="b" * 64,
            format_version=3,
            trust=ArtifactTrust.VERIFIED,
            complete=True,
            signature_valid=True,
        )
        self.prod_point = replace(
            self.stage_point,
            dataset_id="ai-sahakar-prod-v2",
            generation_id="prod-legacy-1",
            manifest_sha256="c" * 64,
        )

    def compile(self, intent, passport=None, **request_overrides):
        return compile_lifecycle_plan(
            LifecycleRequest(intent, **request_overrides),
            self.stage,
            self.capabilities,
            passport,
        )

    def test_first_backup_is_logically_full_and_uploads_every_object(self):
        plan = self.compile(LifecycleIntent.BACKUP)
        self.assertTrue(plan.allowed)
        self.assertEqual(plan.route, LifecycleRoute.BACKUP.value)
        self.assertEqual(plan.transfer_mode, "full_upload")
        self.assertIn("logically_full_manifest", plan.reason_codes)

    def test_later_backup_is_still_logically_full_but_transfers_only_deltas(self):
        plan = self.compile(
            LifecycleIntent.BACKUP,
            previous_recovery_point_exists=True,
        )
        self.assertEqual(plan.transfer_mode, "incremental_deduplicated")
        self.assertIn("logically_full_manifest", plan.reason_codes)

    def test_backup_works_in_stage_but_requires_owned_store_write_capability(self):
        blocked = compile_lifecycle_plan(
            LifecycleRequest(LifecycleIntent.BACKUP),
            self.stage,
            replace(self.capabilities, owned_store_writable=False),
        )
        self.assertFalse(blocked.allowed)
        self.assertIn("owned_store_not_writable", blocked.refusal_codes)

    def test_same_dataset_restore_is_normal_not_an_environment_exception(self):
        plan = self.compile(
            LifecycleIntent.RESTORE,
            self.stage_point,
            activate=True,
            confirmation_present=True,
        )
        self.assertTrue(plan.allowed)
        self.assertEqual(plan.route, LifecycleRoute.SAME_DATASET_RESTORE.value)
        self.assertNotIn("same_dataset_restore_enabled", plan.gates)

    def test_foreign_dataset_restore_automatically_imports_and_rebinds(self):
        plan = self.compile(
            LifecycleIntent.RESTORE,
            self.prod_point,
            activate=True,
            confirmation_present=True,
        )
        self.assertTrue(plan.allowed)
        self.assertEqual(plan.route, LifecycleRoute.IMPORT_REBIND_RESTORE.value)
        self.assertIn("automatic_clone_rebind", plan.reason_codes)
        self.assertIn("rewrite_dataset_bindings", plan.steps)

    def test_operator_never_needs_to_choose_clone_rebind(self):
        intents = {item.value for item in LifecycleIntent}
        self.assertNotIn("clone_rebind", intents)

    def test_unverified_legacy_mount_is_canonicalized_before_restore(self):
        legacy = ArtifactPassport(
            source_kind=SourceKind.LEGACY_MOUNT,
            trust=ArtifactTrust.UNKNOWN,
            complete=False,
            read_only=True,
        )
        plan = self.compile(LifecycleIntent.IMPORT, legacy)
        self.assertTrue(plan.allowed)
        self.assertEqual(plan.route, LifecycleRoute.LEGACY_IMPORT.value)
        self.assertIn("scan_source_twice", plan.steps)
        self.assertIn("stable_two_scan_snapshot", plan.gates)

    def test_writable_legacy_source_is_refused(self):
        legacy = ArtifactPassport(
            source_kind=SourceKind.LEGACY_MOUNT,
            read_only=False,
        )
        plan = self.compile(LifecycleIntent.IMPORT, legacy)
        self.assertFalse(plan.allowed)
        self.assertIn("source_must_be_read_only", plan.refusal_codes)

    def test_v3_recovery_point_requires_a_valid_signature(self):
        plan = self.compile(
            LifecycleIntent.RESTORE,
            replace(self.stage_point, signature_valid=None),
        )
        self.assertFalse(plan.allowed)
        self.assertIn("manifest_signature_missing", plan.refusal_codes)

    def test_invalid_signature_is_refused(self):
        plan = self.compile(
            LifecycleIntent.RESTORE,
            replace(self.stage_point, signature_valid=False),
        )
        self.assertFalse(plan.allowed)
        self.assertIn("manifest_signature_invalid", plan.refusal_codes)

    def test_restore_never_targets_the_active_generation(self):
        plan = self.compile(LifecycleIntent.RESTORE, self.stage_point)
        self.assertIn("restore_to_new_quarantine_generation", plan.steps)

    def test_already_active_generation_is_an_idempotent_noop(self):
        active = replace(
            self.stage_point,
            generation_id=self.stage.active_generation_id,
            manifest_sha256=self.stage.active_manifest_sha256,
        )
        plan = self.compile(
            LifecycleIntent.RESTORE,
            active,
            activate=True,
        )
        self.assertTrue(plan.allowed)
        self.assertEqual(plan.route, LifecycleRoute.NOOP.value)
        self.assertIn("generation_already_active", plan.reason_codes)

    def test_activation_requires_confirmation_signing_and_atomic_runtime_capability(self):
        plan = compile_lifecycle_plan(
            LifecycleRequest(LifecycleIntent.RESTORE, activate=True),
            self.stage,
            replace(
                self.capabilities,
                signing_available=False,
                activation_available=False,
            ),
            self.stage_point,
        )
        self.assertFalse(plan.allowed)
        self.assertEqual(
            set(plan.refusal_codes),
            {
                "activation_unavailable",
                "activation_signing_unavailable",
                "operator_confirmation_required",
            },
        )

    def test_production_restore_adds_prebackup_mutation_barrier_and_rollback_gates(self):
        production = replace(
            self.stage,
            environment="production",
            deployment_id="prod-2026",
            dataset_id="ai-sahakar-prod-2026",
        )
        source = replace(self.stage_point, dataset_id=production.dataset_id)
        plan = compile_lifecycle_plan(
            LifecycleRequest(
                LifecycleIntent.RESTORE,
                activate=True,
                confirmation_present=True,
            ),
            production,
            self.capabilities,
            source,
        )
        self.assertTrue(plan.allowed)
        self.assertIn("publish_pre_restore_recovery_point", plan.steps)
        self.assertIn("enter_mutation_barrier", plan.steps)
        self.assertIn("pre_restore_backup", plan.gates)
        self.assertIn("rollback_authority", plan.gates)

    def test_test_recovery_is_isolated_and_can_never_activate(self):
        plan = self.compile(
            LifecycleIntent.TEST_RECOVERY,
            self.stage_point,
            activate=True,
            confirmation_present=True,
        )
        self.assertTrue(plan.allowed)
        self.assertEqual(plan.route, LifecycleRoute.ISOLATED_REHEARSAL.value)
        self.assertEqual(plan.activation, "none")
        self.assertNotIn("compare_and_swap_runtime_pointer", plan.steps)

    def test_test_recovery_refuses_missing_isolated_workspace(self):
        plan = compile_lifecycle_plan(
            LifecycleRequest(LifecycleIntent.TEST_RECOVERY),
            self.stage,
            replace(self.capabilities, isolated_restore_available=False),
            self.stage_point,
        )
        self.assertFalse(plan.allowed)
        self.assertIn("isolated_restore_unavailable", plan.refusal_codes)

    def test_rollback_is_a_signed_image_generation_pair_swap(self):
        previous = replace(
            self.stage_point,
            source_kind=SourceKind.PREVIOUS_RUNTIME,
        )
        plan = self.compile(
            LifecycleIntent.ROLLBACK,
            previous,
            confirmation_present=True,
        )
        self.assertTrue(plan.allowed)
        self.assertIn("verify_previous_runtime_pair", plan.steps)
        self.assertIn("image_generation_compatibility", plan.gates)
        self.assertEqual(plan.activation, "signed_atomic")

    def test_incomplete_identity_fails_closed(self):
        plan = compile_lifecycle_plan(
            LifecycleRequest(LifecycleIntent.BACKUP),
            InstanceIdentity("stage", "", ""),
            self.capabilities,
        )
        self.assertFalse(plan.allowed)
        self.assertIn("deployment_identity_missing", plan.refusal_codes)
        self.assertIn("target_dataset_missing", plan.refusal_codes)

    def test_plan_is_deterministic_and_contains_no_credentials(self):
        first = self.compile(LifecycleIntent.RESTORE, self.prod_point)
        second = self.compile(LifecycleIntent.RESTORE, self.prod_point)
        self.assertEqual(first, second)
        self.assertEqual(len(first.plan_digest), 64)
        serialized = str(first.as_dict()).lower()
        self.assertNotIn("access_key", serialized)
        self.assertNotIn("secret", serialized)


if __name__ == "__main__":
    unittest.main()
