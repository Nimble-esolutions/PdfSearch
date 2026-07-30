import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from django.db import close_old_connections
from django.test import TransactionTestCase, override_settings
from django.urls import reverse

from core.models import CustomUser
from vaultops.models import (
    SourceSnapshot,
    SourceMutationState,
    VaultAuditEvent,
    VaultJob,
    VaultJobRetryRequest,
)
from vaultops.services.jobs import requeue_job
from vaultops.services.lifecycle import LifecycleConflict
from vaultops.services.read_model import _job_records
from vaultops.services.snapshot import SnapshotError, cleanup_snapshot_workspace


@override_settings(VAULT_SNAPSHOT_ROOT="")
class SyncRetryHardeningTests(TransactionTestCase):
    databases = {"default", "control"}

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.override = override_settings(VAULT_SNAPSHOT_ROOT=self.root)
        self.override.enable()
        self.user = CustomUser.objects.create_user(
            username="retry-operator",
            password="test-only",
            role="superadmin",
        )

    def tearDown(self):
        self.override.disable()
        self.temporary.cleanup()
        super().tearDown()

    def _job(self, *, operation="sync_publish"):
        return VaultJob.objects.create(
            operation=operation,
            status=VaultJob.Status.RETRYABLE_FAILED,
            idempotency_key=f"original-{operation}",
            requested_by_id=self.user.pk,
            requested_by_name=self.user.get_username(),
        )

    def _failed_snapshot(self, job, *, state=SourceSnapshot.State.FAILED):
        snapshot = SourceSnapshot.objects.create(
            job=job,
            deployment_id="test",
            state=state,
            initial_epoch=1,
        )
        workspace = self.root / f".{snapshot.public_id}.incomplete"
        workspace.mkdir()
        (workspace / "partial.bin").write_bytes(b"partial")
        snapshot.workspace_path = str(workspace)
        snapshot.save(update_fields=["workspace_path", "updated_at"])
        return snapshot, workspace

    def test_fresh_snapshot_retry_is_idempotent_and_cleans_failed_workspace(self):
        job = self._job()
        snapshot, workspace = self._failed_snapshot(job)

        first, receipt, created = requeue_job(
            job.public_id,
            expected_state_version=job.state_version,
            idempotency_key="retry-fresh-0001",
            actor_id=self.user.pk,
            actor_name=self.user.get_username(),
        )
        second, replay, replay_created = requeue_job(
            job.public_id,
            expected_state_version=job.state_version,
            idempotency_key="retry-fresh-0001",
            actor_id=self.user.pk,
            actor_name=self.user.get_username(),
        )

        self.assertTrue(created)
        self.assertFalse(replay_created)
        self.assertEqual(receipt.pk, replay.pk)
        self.assertEqual(first.retry_count, 1)
        self.assertEqual(second.retry_count, 1)
        self.assertEqual(receipt.mode, VaultJobRetryRequest.Mode.FRESH_SNAPSHOT)
        self.assertFalse(workspace.exists())
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.workspace_path, "")
        self.assertEqual(
            snapshot.evidence["workspace_cleanup"]["status"], "removed"
        )
        self.assertEqual(
            VaultAuditEvent.objects.filter(
                job_public_id=job.public_id,
                action="job_requeued",
            ).count(),
            1,
        )

    def test_finalized_snapshot_retry_truthfully_resumes_checkpoint(self):
        job = self._job()
        final = self.root / "final"
        final.mkdir()
        SourceSnapshot.objects.create(
            job=job,
            deployment_id="test",
            state=SourceSnapshot.State.FINALIZED,
            workspace_path=str(final),
            snapshot_digest="a" * 64,
        )

        _, receipt, _ = requeue_job(
            job.public_id,
            expected_state_version=job.state_version,
            idempotency_key="retry-checkpoint-0001",
            actor_id=self.user.pk,
        )

        self.assertEqual(
            receipt.mode, VaultJobRetryRequest.Mode.CHECKPOINT_RESUME
        )
        self.assertTrue(final.exists())

    def test_read_model_distinguishes_fresh_snapshot_and_checkpoint_resume(self):
        fresh = self._job()
        resumed = self._job(operation="restore_generation")

        records = {
            record["public_id"]: record
            for record in _job_records(None, "", limit=10)
        }

        self.assertEqual(records[str(fresh.public_id)]["retry_mode"], "fresh_snapshot")
        self.assertEqual(
            records[str(resumed.public_id)]["retry_mode"],
            "checkpoint_resume",
        )
        self.assertNotEqual(
            records[str(fresh.public_id)]["retry_action_label"],
            records[str(resumed.public_id)]["retry_action_label"],
        )

    @override_settings(VAULT_ADMIN_MUTATIONS_ENABLED=True)
    def test_retry_post_replay_returns_original_receipt_without_second_audit(self):
        job = self._job()
        self.client.force_login(self.user)
        payload = {
            "idempotency_key": "retry-http-replay-0001",
            "job_state_version": str(job.state_version),
        }
        url = reverse("vaultops:job_retry", args=[job.public_id])

        first = self.client.post(
            url, payload, HTTP_ACCEPT="application/json"
        )
        second = self.client.post(
            url, payload, HTTP_ACCEPT="application/json"
        )

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertFalse(first.json()["data"]["idempotent_replay"])
        self.assertTrue(second.json()["data"]["idempotent_replay"])
        self.assertEqual(
            first.json()["state_version"], second.json()["state_version"]
        )
        self.assertEqual(
            VaultAuditEvent.objects.filter(action="job_requeued").count(), 1
        )

    def test_stale_state_and_conflicting_replay_change_nothing(self):
        job = self._job()
        with self.assertRaises(LifecycleConflict):
            requeue_job(
                job.public_id,
                expected_state_version=job.state_version + 1,
                idempotency_key="retry-stale-0001",
                actor_id=self.user.pk,
            )
        self.assertEqual(VaultJobRetryRequest.objects.count(), 0)
        self.assertEqual(VaultAuditEvent.objects.count(), 0)
        job.refresh_from_db()
        self.assertEqual(job.retry_count, 0)

    def test_concurrent_duplicate_retry_has_one_counter_and_audit_event(self):
        job = self._job()
        barrier = threading.Barrier(2)

        def submit():
            close_old_connections()
            barrier.wait()
            try:
                return requeue_job(
                    job.public_id,
                    expected_state_version=job.state_version,
                    idempotency_key="retry-concurrent-0001",
                    actor_id=self.user.pk,
                    actor_name=self.user.get_username(),
                )[2]
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            created = list(executor.map(lambda _item: submit(), range(2)))

        job.refresh_from_db()
        self.assertEqual(sorted(created), [False, True])
        self.assertEqual(job.retry_count, 1)
        self.assertEqual(VaultJobRetryRequest.objects.count(), 1)
        self.assertEqual(
            VaultAuditEvent.objects.filter(action="job_requeued").count(), 1
        )

    def test_hard_kill_copying_workspace_is_reclaimed_before_retry(self):
        job = self._job()
        snapshot, workspace = self._failed_snapshot(
            job, state=SourceSnapshot.State.COPYING
        )
        SourceMutationState.objects.create(
            deployment_id="local",
            barrier_state=SourceMutationState.BarrierState.ACTIVE,
            barrier_owner_job=job.public_id,
        )

        requeue_job(
            job.public_id,
            expected_state_version=job.state_version,
            idempotency_key="retry-hard-kill-0001",
            actor_id=self.user.pk,
        )

        self.assertFalse(workspace.exists())
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.workspace_path, "")
        state = SourceMutationState.objects.get(deployment_id="local")
        self.assertEqual(
            state.barrier_state, SourceMutationState.BarrierState.OPEN
        )
        self.assertIsNone(state.barrier_owner_job)

    def test_cleanup_refuses_path_outside_snapshot_root(self):
        job = self._job()
        snapshot = SourceSnapshot.objects.create(
            job=job,
            deployment_id="test",
            state=SourceSnapshot.State.FAILED,
            workspace_path="/tmp/not-owned.incomplete",
        )
        with self.assertRaisesRegex(
            SnapshotError, "snapshot_cleanup_path_invalid"
        ):
            cleanup_snapshot_workspace(snapshot, reason="test")
