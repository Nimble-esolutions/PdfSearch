from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from vaultops.models import (
    ArtifactGeneration,
    GarbageCollectionPlan,
    RetentionHold,
    VaultConnectionProfile,
    VaultDatasetProjection,
    VaultJob,
)
from vaultops.services.retention import (
    RetentionError,
    create_gc_plan,
    create_retention_hold,
    execute_gc_plan,
    release_retention_hold,
    retire_generation,
    unretire_generation,
)


@override_settings(
    VAULT_GC_ENABLED=False,
    VAULT_GC_GRACE_DAYS=1,
    VAULT_INVENTORY_CACHE_SECONDS=60,
)
class RetentionServiceTests(TestCase):
    databases = {"default", "control"}

    def setUp(self):
        self.now = timezone.now()
        self.profile = VaultConnectionProfile.objects.create(
            key="retention-test",
            display_name="Retention test",
            source=VaultConnectionProfile.Source.ENVIRONMENT,
            enabled=True,
            read_only=False,
            environment_locked=True,
            dataset_id="ai-sahakar-test",
            fingerprint="f" * 64,
        )
        self.projection = VaultDatasetProjection.objects.create(
            profile=self.profile,
            dataset_id=self.profile.dataset_id,
            registration_digest="a" * 64,
            pointer_digest="b" * 64,
            authoritative_generation_id="generation-current",
            inventory_state="verified",
            inventory_observed_at=self.now,
        )

    def make_generation(self, generation_id, *, state="candidate", files=None):
        return ArtifactGeneration.objects.create(
            profile=self.profile,
            dataset_id=self.profile.dataset_id,
            generation_id=generation_id,
            manifest_digest=("c" if state == "retired" else "d") * 64,
            manifest={"files": files or []},
            vault_state=state,
            runtime_state=ArtifactGeneration.RuntimeState.INACTIVE,
            local_presence=ArtifactGeneration.LocalPresence.ABSENT,
        )

    def age_generation(self, generation, *, days=2):
        ArtifactGeneration.objects.filter(pk=generation.pk).update(
            updated_at=self.now - timedelta(days=days)
        )
        generation.refresh_from_db()

    def test_retirement_fails_closed_on_remote_authority_projection(self):
        generation = self.make_generation("generation-current")

        with self.assertRaises(RetentionError) as raised:
            retire_generation(generation)

        self.assertEqual(raised.exception.reason_code, "generation_authoritative")
        generation.refresh_from_db()
        self.assertEqual(generation.vault_state, "candidate")

    def test_retirement_and_unretirement_are_reversible(self):
        generation = self.make_generation("generation-old")

        retired = retire_generation(
            generation, actor_id=7, actor_name="operator"
        )
        self.assertEqual(retired.vault_state, "retired")

        restored = unretire_generation(
            retired, actor_id=7, actor_name="operator"
        )
        self.assertEqual(restored.vault_state, "candidate")

    def test_running_job_blocks_retirement(self):
        generation = self.make_generation("generation-busy")
        VaultJob.objects.create(
            operation="restore_generation",
            profile=self.profile,
            dataset_id=self.profile.dataset_id,
            generation_id=generation.generation_id,
            idempotency_key="retention-running-job",
        )

        with self.assertRaises(RetentionError) as raised:
            retire_generation(generation)

        self.assertEqual(
            raised.exception.reason_code, "generation_job_in_progress"
        )

    def test_retention_hold_blocks_gc_candidate(self):
        generation = self.make_generation(
            "generation-held", state="retired"
        )
        self.age_generation(generation)
        create_retention_hold(
            generation,
            reason_code="incident",
            owner_reference="INC-17",
        )

        with self.assertRaises(RetentionError) as raised:
            create_gc_plan(
                self.profile,
                dataset_id=self.profile.dataset_id,
                now=self.now,
            )

        self.assertEqual(
            raised.exception.reason_code, "gc_no_eligible_candidates"
        )
        self.assertTrue(
            RetentionHold.objects.filter(generation=generation).exists()
        )

    def test_retention_hold_release_is_audited_and_idempotent(self):
        generation = self.make_generation("generation-held")
        hold = create_retention_hold(
            generation,
            reason_code="incident",
            owner_reference="INC-18",
        )

        released = release_retention_hold(hold, actor_name="operator")
        released_again = release_retention_hold(
            released, actor_name="operator"
        )

        self.assertIsNotNone(released.released_at)
        self.assertEqual(released_again.released_at, released.released_at)

    def test_gc_plan_separates_shared_and_exclusive_objects(self):
        shared_key = (
            "datasets/ai-sahakar-test/objects/pdf/" + "1" * 64
        )
        exclusive_key = (
            "datasets/ai-sahakar-test/objects/pdf/" + "2" * 64
        )
        retired = self.make_generation(
            "generation-retired",
            state="retired",
            files=[
                {
                    "object_key": shared_key,
                    "sha256": "1" * 64,
                    "bytes": 10,
                },
                {
                    "object_key": exclusive_key,
                    "sha256": "2" * 64,
                    "bytes": 20,
                },
            ],
        )
        self.age_generation(retired)
        self.make_generation(
            "generation-current",
            files=[
                {
                    "object_key": shared_key,
                    "sha256": "1" * 64,
                    "bytes": 10,
                }
            ],
        )

        plan = create_gc_plan(
            self.profile,
            dataset_id=self.profile.dataset_id,
            actor_id=9,
            actor_name="operator",
            now=self.now,
        )

        self.assertEqual(plan.state, GarbageCollectionPlan.State.READY)
        self.assertEqual(plan.estimates["candidate_generations"], 1)
        self.assertEqual(plan.estimates["exclusive_objects"], 1)
        self.assertEqual(plan.estimates["exclusive_bytes"], 20)
        self.assertEqual(plan.estimates["shared_objects"], 1)
        self.assertFalse(plan.estimates["deletion_enabled"])
        self.assertEqual(
            plan.candidates[0]["exclusive_object_keys"], [exclusive_key]
        )
        self.assertEqual(
            plan.candidates[0]["shared_object_keys"], [shared_key]
        )

    def test_stale_inventory_blocks_gc_planning(self):
        generation = self.make_generation(
            "generation-retired", state="retired"
        )
        self.age_generation(generation)
        self.projection.inventory_observed_at = self.now - timedelta(minutes=2)
        self.projection.save(update_fields=["inventory_observed_at"])

        with self.assertRaises(RetentionError) as raised:
            create_gc_plan(
                self.profile,
                dataset_id=self.profile.dataset_id,
                now=self.now,
            )

        self.assertEqual(raised.exception.reason_code, "gc_inventory_stale")

    def test_gc_plan_rejects_cross_dataset_object_key(self):
        generation = self.make_generation(
            "generation-retired",
            state="retired",
            files=[
                {
                    "object_key": "datasets/other-dataset/objects/pdf/" + "8" * 64,
                    "sha256": "8" * 64,
                    "bytes": 10,
                }
            ],
        )
        self.age_generation(generation)

        with self.assertRaises(RetentionError) as raised:
            create_gc_plan(
                self.profile,
                dataset_id=self.profile.dataset_id,
                now=self.now,
            )

        self.assertEqual(
            raised.exception.reason_code, "gc_reference_graph_invalid"
        )

    def test_gc_execution_remains_hard_disabled(self):
        generation = self.make_generation(
            "generation-retired", state="retired"
        )
        self.age_generation(generation)
        plan = create_gc_plan(
            self.profile,
            dataset_id=self.profile.dataset_id,
            now=self.now,
        )

        with self.assertRaises(RetentionError) as raised:
            execute_gc_plan(plan)

        self.assertEqual(raised.exception.reason_code, "gc_execution_disabled")

    def test_metrics_report_retention_and_gc_without_object_keys(self):
        generation = self.make_generation(
            "generation-retired",
            state="retired",
            files=[
                {
                    "object_key": "datasets/ai-sahakar-test/objects/pdf/" + "9" * 64,
                    "sha256": "9" * 64,
                    "bytes": 21,
                }
            ],
        )
        self.age_generation(generation)
        create_retention_hold(
            self.make_generation("generation-held"),
            reason_code="incident",
            owner_reference="INC-19",
        )
        create_gc_plan(
            self.profile,
            dataset_id=self.profile.dataset_id,
            now=self.now,
        )

        response = self.client.get("/health/metrics/")
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("pdfsearch_vault_retired_generation_count 1.0", body)
        self.assertIn("pdfsearch_vault_active_retention_hold_count 1.0", body)
        self.assertIn("pdfsearch_vault_gc_ready_plan_count 1.0", body)
        self.assertIn("pdfsearch_vault_gc_planned_exclusive_bytes 21.0", body)
        self.assertIn("pdfsearch_vault_gc_execution_enabled 0.0", body)
        self.assertNotIn("objects/pdf/", body)
