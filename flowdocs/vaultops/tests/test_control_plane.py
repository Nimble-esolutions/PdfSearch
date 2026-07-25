import importlib
import uuid
from datetime import timedelta
from types import SimpleNamespace

from django.apps import apps
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from core.models import (
    ArtifactGeneration as LegacyArtifactGeneration,
    ArtifactValidation as LegacyArtifactValidation,
    CustomUser,
    MaintenanceAuditEvent,
    MaintenanceJob,
)
from vaultops.models import (
    ArtifactGeneration,
    RestoreWorkspace,
    RuntimePointerObservation,
    VaultAuditEvent,
    VaultConnectionProfile,
    VaultDatasetProjection,
    VaultJob,
)
from vaultops.router import VaultControlRouter
from vaultops.services.jobs import (
    ClaimRejected,
    claim_job,
    heartbeat_job,
    recover_stale_jobs,
    requeue_job,
    start_job,
)
from vaultops.services.lifecycle import (
    LifecycleConflict,
    transition_generation_runtime_state,
    transition_generation_vault_state,
    transition_workspace,
)
from vaultops.services.read_model import build_authority_state


class ControlPlaneTestCase(TestCase):
    databases = {"default", "control"}

    def setUp(self):
        self.profile = VaultConnectionProfile.objects.create(
            key="test-profile",
            display_name="Test profile",
            source=VaultConnectionProfile.Source.ENVIRONMENT,
            dataset_id="ai-sahakar-test",
            environment_locked=True,
        )

    def make_generation(self, generation_id="generation-1", **kwargs):
        values = {
            "profile": self.profile,
            "dataset_id": "ai-sahakar-test",
            "generation_id": generation_id,
            "manifest_digest": "a" * 64,
        }
        values.update(kwargs)
        return ArtifactGeneration.objects.create(**values)


class DatabaseRouterTests(ControlPlaneTestCase):
    def test_vaultops_reads_and_writes_route_only_to_control_database(self):
        router = VaultControlRouter()

        self.assertEqual(router.db_for_read(ArtifactGeneration), "control")
        self.assertEqual(router.db_for_write(ArtifactGeneration), "control")
        self.assertTrue(router.allow_migrate("control", "vaultops"))
        self.assertFalse(router.allow_migrate("default", "vaultops"))
        self.assertFalse(router.allow_migrate("control", "core"))

    def test_cross_database_relations_are_rejected(self):
        router = VaultControlRouter()
        legacy = LegacyArtifactGeneration(generation_id="legacy")
        projected = self.make_generation()

        self.assertFalse(router.allow_relation(legacy, projected))
        self.assertTrue(router.allow_relation(projected, self.profile))


class LifecycleGuardTests(ControlPlaneTestCase):
    def test_candidate_can_become_authoritative_and_emits_audit_event(self):
        generation = self.make_generation(
            vault_state=ArtifactGeneration.VaultState.CANDIDATE
        )
        correlation_id = uuid.uuid4()

        transitioned = transition_generation_vault_state(
            generation,
            ArtifactGeneration.VaultState.AUTHORITATIVE,
            correlation_id=correlation_id,
            actor_id=17,
            actor_name="operator",
            reason_code="pointer_cas_succeeded",
        )

        self.assertEqual(
            transitioned.vault_state, ArtifactGeneration.VaultState.AUTHORITATIVE
        )
        event = VaultAuditEvent.objects.get(
            action="generation_vault_state_changed"
        )
        self.assertEqual(event.correlation_id, correlation_id)
        self.assertEqual(event.before_state, {"vault_state": "candidate"})
        self.assertEqual(event.after_state, {"vault_state": "authoritative"})

    def test_legacy_read_only_generation_cannot_be_promoted(self):
        generation = self.make_generation(
            vault_state=ArtifactGeneration.VaultState.LEGACY_READ_ONLY
        )

        with self.assertRaises(LifecycleConflict) as raised:
            transition_generation_vault_state(
                generation,
                ArtifactGeneration.VaultState.AUTHORITATIVE,
                correlation_id=uuid.uuid4(),
            )

        self.assertEqual(raised.exception.reason_code, "invalid_vault_transition")

    def test_workspace_cannot_skip_from_planned_to_activation_ready(self):
        generation = self.make_generation()
        workspace = RestoreWorkspace.objects.create(
            generation=generation,
            manifest_digest=generation.manifest_digest,
        )

        with self.assertRaises(LifecycleConflict) as raised:
            transition_workspace(
                workspace,
                RestoreWorkspace.State.ACTIVATION_READY,
                correlation_id=uuid.uuid4(),
            )

        self.assertEqual(
            raised.exception.reason_code, "invalid_workspace_transition"
        )

    def test_only_one_runtime_generation_can_be_active_per_deployment(self):
        first = self.make_generation(
            generation_id="runtime-1",
            deployment_id="staging-01",
            runtime_state=ArtifactGeneration.RuntimeState.APPLYING,
        )
        transition_generation_runtime_state(
            first,
            ArtifactGeneration.RuntimeState.ACTIVE,
            correlation_id=uuid.uuid4(),
        )
        second = self.make_generation(
            generation_id="runtime-2",
            deployment_id="staging-01",
            runtime_state=ArtifactGeneration.RuntimeState.APPLYING,
        )

        with self.assertRaises(IntegrityError), transaction.atomic(
            using="control"
        ):
            transition_generation_runtime_state(
                second,
                ArtifactGeneration.RuntimeState.ACTIVE,
                correlation_id=uuid.uuid4(),
            )


class AppendOnlyAuditTests(ControlPlaneTestCase):
    def setUp(self):
        super().setUp()
        self.event = VaultAuditEvent.objects.create(
            action="test_event",
            result="recorded",
            correlation_id=uuid.uuid4(),
        )

    def test_event_cannot_be_updated(self):
        self.event.result = "changed"
        with self.assertRaises(TypeError):
            self.event.save()
        with self.assertRaises(TypeError):
            VaultAuditEvent.objects.filter(pk=self.event.pk).update(
                result="changed"
            )

    def test_event_cannot_be_deleted(self):
        with self.assertRaises(TypeError):
            self.event.delete()
        with self.assertRaises(TypeError):
            VaultAuditEvent.objects.filter(pk=self.event.pk).delete()


class DurableJobOwnershipTests(ControlPlaneTestCase):
    def make_job(self, idempotency_key="request-1"):
        return VaultJob.objects.create(
            operation="publish_generation",
            profile=self.profile,
            dataset_id="ai-sahakar-test",
            generation_id="generation-1",
            idempotency_key=idempotency_key,
        )

    def test_claim_hashes_token_and_heartbeats_require_matching_fence(self):
        job = self.make_job()

        claimed, token = claim_job(job.public_id, worker_id="worker-a")
        self.assertNotEqual(claimed.claim_token_hash, token)
        self.assertNotIn(token, claimed.claim_token_hash)

        running = start_job(
            job.public_id,
            token=token,
            fencing_epoch=claimed.fencing_epoch,
        )
        heartbeated = heartbeat_job(
            job.public_id,
            token=token,
            fencing_epoch=claimed.fencing_epoch,
            phase="uploading",
            progress={"objects": 3},
        )

        self.assertEqual(running.status, VaultJob.Status.RUNNING)
        self.assertEqual(heartbeated.phase, "uploading")
        self.assertEqual(heartbeated.progress, {"objects": 3})
        self.assertTrue(
            VaultAuditEvent.objects.filter(
                job_public_id=job.public_id, action="job_started"
            ).exists()
        )

    def test_stale_owner_is_fenced_after_recovery_and_reclaim(self):
        job = self.make_job()
        old_time = timezone.now() - timedelta(minutes=5)
        claimed, old_token = claim_job(
            job.public_id, worker_id="worker-old", now=old_time
        )
        start_job(
            job.public_id,
            token=old_token,
            fencing_epoch=claimed.fencing_epoch,
            now=old_time,
        )

        recovered = recover_stale_jobs(stale_seconds=90)
        self.assertEqual(recovered, [job.public_id])
        requeue_job(job.public_id)
        reclaimed, new_token = claim_job(job.public_id, worker_id="worker-new")

        self.assertGreater(reclaimed.fencing_epoch, claimed.fencing_epoch)
        self.assertNotEqual(new_token, old_token)
        with self.assertRaises(ClaimRejected) as raised:
            heartbeat_job(
                job.public_id,
                token=old_token,
                fencing_epoch=claimed.fencing_epoch,
            )
        self.assertEqual(raised.exception.reason_code, "stale_job_owner")

    def test_idempotency_is_scoped_to_operation(self):
        self.make_job()
        with self.assertRaises(IntegrityError), transaction.atomic(
            using="control"
        ):
            self.make_job()


class AuthorityReadModelTests(ControlPlaneTestCase):
    def test_remote_authority_and_runtime_activity_remain_independent(self):
        remote = self.make_generation(
            generation_id="remote-authoritative",
            vault_state=ArtifactGeneration.VaultState.AUTHORITATIVE,
            runtime_state=ArtifactGeneration.RuntimeState.INACTIVE,
        )
        runtime = self.make_generation(
            generation_id="runtime-active",
            vault_state=ArtifactGeneration.VaultState.CANDIDATE,
            runtime_state=ArtifactGeneration.RuntimeState.ACTIVE,
            deployment_id="staging-01",
        )
        now = timezone.now()
        VaultDatasetProjection.objects.create(
            profile=self.profile,
            dataset_id="ai-sahakar-test",
            authoritative_generation_id=remote.generation_id,
            pointer_digest="b" * 64,
            inventory_state="verified",
            inventory_observed_at=now,
        )
        RuntimePointerObservation.objects.create(
            deployment_id="staging-01",
            active_generation_id=runtime.generation_id,
            status="ready",
            observed_at=now,
        )

        state = build_authority_state(
            profile_key=self.profile.key,
            dataset_id="ai-sahakar-test",
            deployment_id="staging-01",
        )

        self.assertEqual(state["status"], "healthy")
        self.assertEqual(
            state["remote"]["authoritative_generation_id"],
            "remote-authoritative",
        )
        self.assertEqual(
            state["runtime"]["active_generation_id"], "runtime-active"
        )
        self.assertNotEqual(
            state["remote"]["authoritative_generation_id"],
            state["runtime"]["active_generation_id"],
        )

    def test_missing_observations_are_unknown_not_healthy(self):
        state = build_authority_state(
            profile_key=self.profile.key,
            dataset_id="ai-sahakar-test",
            deployment_id="staging-01",
        )

        self.assertEqual(state["status"], "degraded")
        self.assertEqual(state["remote"]["state"], "unknown")
        self.assertEqual(state["runtime"]["state"], "unknown")
        self.assertIn("inventory_unavailable", state["blocking_reasons"])


class LegacyBackfillTests(ControlPlaneTestCase):
    def test_legacy_active_is_projected_as_runtime_unknown(self):
        user = CustomUser.objects.create_user(
            username="legacy-operator",
            password="not-used",
            role="superadmin",
        )
        legacy_generation = LegacyArtifactGeneration.objects.create(
            generation_id="legacy-generation",
            status="active",
            manifest={"dataset_id": "ai-sahakar-test"},
            source="s3",
            created_by=user,
        )
        LegacyArtifactValidation.objects.create(
            generation=legacy_generation,
            validation_type="manifest",
            status="passed",
            details={"manifest_digest": "c" * 64},
            validated_by=user,
        )
        legacy_job = MaintenanceJob.objects.create(
            kind="restore_generation",
            status="failed",
            scope={
                "dataset_id": "ai-sahakar-test",
                "generation_id": "legacy-generation",
            },
            error_summary="raw exception must not cross databases",
            requested_by=user,
        )
        MaintenanceAuditEvent.objects.create(
            job=legacy_job,
            event_type="failed",
            actor=user,
            payload={"safe": "legacy evidence"},
        )
        migration = importlib.import_module(
            "vaultops.migrations.0002_backfill_legacy_lifecycle"
        )

        migration.backfill_legacy_lifecycle(
            apps,
            SimpleNamespace(
                connection=SimpleNamespace(alias="control")
            ),
        )
        migration.backfill_legacy_lifecycle(
            apps,
            SimpleNamespace(
                connection=SimpleNamespace(alias="control")
            ),
        )

        projected = ArtifactGeneration.objects.get(
            legacy_database_id=legacy_generation.pk
        )
        self.assertEqual(
            projected.vault_state, ArtifactGeneration.VaultState.CANDIDATE
        )
        self.assertEqual(
            projected.runtime_state, ArtifactGeneration.RuntimeState.UNKNOWN
        )
        self.assertEqual(projected.legacy_status, "active")

        projected_job = VaultJob.objects.get(legacy_database_id=legacy_job.pk)
        self.assertEqual(projected_job.status, VaultJob.Status.TERMINAL_FAILED)
        self.assertEqual(projected_job.safe_error_code, "legacy_job_failed")
        self.assertNotIn("raw exception", str(projected_job.progress))
        self.assertEqual(
            VaultAuditEvent.objects.filter(
                action="legacy_active_projection_recorded"
            ).count(),
            1,
        )
