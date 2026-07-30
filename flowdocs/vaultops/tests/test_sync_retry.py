import hashlib
import json
import os
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from django.db import close_old_connections, connections, transaction
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import CustomUser
from vaultops.models import (
    SourceSnapshot,
    SourceMutationState,
    VaultAuditEvent,
    VaultJob,
    VaultJobRetryRequest,
    VaultJobStep,
)
from vaultops.services.jobs import requeue_job
from vaultops.services.lifecycle import LifecycleConflict
from vaultops.services.read_model import _job_records
from vaultops.services.snapshot import (
    SnapshotError,
    create_consistent_snapshot,
    reclaim_snapshot_cleanup_intents,
    snapshot_configuration_fingerprint,
    stage_snapshot_cleanup,
)


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
            idempotency_key=f"original-{operation}-{uuid.uuid4()}",
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

    def _finalized_snapshot(
        self,
        job,
        *,
        trusted_fingerprint=None,
        file_fingerprint=None,
        primary_fingerprint=None,
        checkpoint_overrides=None,
        configuration_bytes=None,
        primary_bytes=None,
        database_posture="valid",
        rebind_primary_digest=False,
    ):
        fingerprint = snapshot_configuration_fingerprint()
        trusted_fingerprint = (
            fingerprint
            if trusted_fingerprint is None
            else trusted_fingerprint
        )
        file_fingerprint = (
            fingerprint if file_fingerprint is None else file_fingerprint
        )
        primary_fingerprint = (
            fingerprint
            if primary_fingerprint is None
            else primary_fingerprint
        )
        snapshot = SourceSnapshot.objects.create(
            job=job,
            deployment_id="test",
            state=SourceSnapshot.State.FINALIZED,
            initial_epoch=1,
            included_epoch=2,
            snapshot_digest="a" * 64,
            finalized_at=timezone.now(),
            evidence={
                "snapshot_schema": 1,
                "evidence_path": "snapshot-evidence.json",
                "configuration_path": "snapshot-configuration.json",
                "configuration_fingerprint": trusted_fingerprint,
            },
        )
        workspace = (
            self.root
            / f"{snapshot.public_id}-{snapshot.snapshot_digest[:12]}"
        )
        workspace.mkdir()
        database_payload = b"canonical snapshot database"
        database_digest = hashlib.sha256(database_payload).hexdigest()
        database_path = workspace / "db.sqlite3"
        database_path.write_bytes(database_payload)
        primary_path = workspace / "snapshot-evidence.json"
        primary_path.write_text(
            json.dumps(
                {
                    "checkpoint_binding": {
                        "configuration_fingerprint": fingerprint,
                        "database_sha256": database_digest,
                        "included_epoch": snapshot.included_epoch,
                        "initial_epoch": snapshot.initial_epoch,
                        "snapshot_digest": snapshot.snapshot_digest,
                        "snapshot_id": str(snapshot.public_id),
                    },
                    "snapshot_id": str(snapshot.public_id),
                    "snapshot_digest": snapshot.snapshot_digest,
                    "configuration_fingerprint": fingerprint,
                    "database": {"sha256": database_digest},
                }
            ),
            encoding="utf-8",
        )
        (workspace / "snapshot-configuration.json").write_bytes(
            configuration_bytes
            if configuration_bytes is not None
            else json.dumps(file_fingerprint, sort_keys=True).encode("utf-8")
        )
        snapshot.workspace_path = str(workspace)
        snapshot.evidence = {
            **snapshot.evidence,
            "database_sha256": database_digest,
            "evidence_sha256": hashlib.sha256(
                primary_path.read_bytes()
            ).hexdigest(),
        }
        snapshot.save(
            update_fields=["workspace_path", "evidence", "updated_at"]
        )
        if primary_bytes is not None:
            primary_path.write_bytes(primary_bytes)
        elif primary_fingerprint != fingerprint:
            primary_path.write_text(
                json.dumps(
                    {
                        "checkpoint_binding": {
                            "configuration_fingerprint": primary_fingerprint,
                            "database_sha256": database_digest,
                            "included_epoch": snapshot.included_epoch,
                            "initial_epoch": snapshot.initial_epoch,
                            "snapshot_digest": snapshot.snapshot_digest,
                            "snapshot_id": str(snapshot.public_id),
                        },
                        "snapshot_id": str(snapshot.public_id),
                        "snapshot_digest": snapshot.snapshot_digest,
                        "configuration_fingerprint": primary_fingerprint,
                    }
                ),
                encoding="utf-8",
            )
        if rebind_primary_digest:
            snapshot.evidence = {
                **snapshot.evidence,
                "evidence_sha256": hashlib.sha256(
                    primary_path.read_bytes()
                ).hexdigest(),
            }
            snapshot.save(update_fields=["evidence", "updated_at"])
        if database_posture == "missing":
            database_path.unlink()
        elif database_posture == "mutated":
            database_path.write_bytes(b"changed database")
        elif database_posture == "symlink":
            database_path.unlink()
            outside = self.root / f"outside-{snapshot.public_id}.sqlite3"
            outside.write_bytes(database_payload)
            database_path.symlink_to(outside)
        elif database_posture == "internal_symlink":
            database_path.unlink()
            sibling = workspace / "sibling.sqlite3"
            sibling.write_bytes(database_payload)
            database_path.symlink_to(sibling.name)
        elif database_posture == "fifo":
            database_path.unlink()
            os.mkfifo(database_path)
        elif database_posture == "directory":
            database_path.unlink()
            database_path.mkdir()
        checkpoint = {
            "snapshot_id": str(snapshot.public_id),
            "snapshot_digest": snapshot.snapshot_digest,
            "included_epoch": snapshot.included_epoch,
            "configuration_fingerprint_sha256": fingerprint["sha256"],
        }
        checkpoint.update(checkpoint_overrides or {})
        VaultJobStep.objects.create(
            job=job,
            phase="snapshot",
            status=VaultJobStep.Status.COMPLETED,
            checkpoint=checkpoint,
            finished_at=timezone.now(),
        )
        return snapshot, workspace

    def test_fresh_snapshot_retry_is_idempotent_and_records_cleanup(self):
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
        self.assertEqual(snapshot.cleanup_state, SourceSnapshot.CleanupState.COMPLETED)
        self.assertEqual(
            snapshot.evidence["workspace_cleanup"]["status"], "completed"
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
        snapshot, final = self._finalized_snapshot(job)

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
        snapshot.refresh_from_db()
        self.assertEqual(
            snapshot.cleanup_state, SourceSnapshot.CleanupState.NONE
        )

    @override_settings(ARTIFACT_INVENTORY_MAX_MEDIA_FILE_BYTES=1)
    def test_changed_custody_cap_forces_fresh_snapshot_retry(self):
        job = self._job()
        with override_settings(
            ARTIFACT_INVENTORY_MAX_MEDIA_FILE_BYTES=64 * 1024 * 1024
        ):
            snapshot, workspace = self._finalized_snapshot(job)

        _, receipt, _ = requeue_job(
            job.public_id,
            expected_state_version=job.state_version,
            idempotency_key="retry-config-changed-0001",
            actor_id=self.user.pk,
        )

        self.assertEqual(
            receipt.mode, VaultJobRetryRequest.Mode.FRESH_SNAPSHOT
        )
        self.assertFalse(workspace.exists())
        snapshot.refresh_from_db()
        self.assertEqual(
            snapshot.cleanup_state, SourceSnapshot.CleanupState.COMPLETED
        )

    def test_untrusted_snapshot_fingerprints_force_fresh_retry(self):
        cases = {
            "missing": {"trusted_fingerprint": {}},
            "forged": {
                "trusted_fingerprint": {
                    **snapshot_configuration_fingerprint(),
                    "sha256": "f" * 64,
                }
            },
            "malformed_file": {"configuration_bytes": b"{not-json"},
            "missing_primary": {"primary_fingerprint": {}},
            "forged_primary": {
                "primary_fingerprint": {
                    **snapshot_configuration_fingerprint(),
                    "sha256": "e" * 64,
                },
                "rebind_primary_digest": True,
            },
            "malformed_primary": {
                "primary_bytes": b"{not-json",
                "rebind_primary_digest": True,
            },
        }
        for name, fixture_options in cases.items():
            with self.subTest(name=name):
                job = self._job()
                _snapshot, _workspace = self._finalized_snapshot(
                    job, **fixture_options
                )

                _, receipt, _ = requeue_job(
                    job.public_id,
                    expected_state_version=job.state_version,
                    idempotency_key=f"retry-fingerprint-{name}-0001",
                    actor_id=self.user.pk,
                )

                self.assertEqual(
                    receipt.mode, VaultJobRetryRequest.Mode.FRESH_SNAPSHOT
                )

    def test_snapshot_checkpoint_mismatch_forces_fresh_retry(self):
        job = self._job()
        self._finalized_snapshot(
            job,
            checkpoint_overrides={"snapshot_digest": "b" * 64},
        )

        _, receipt, _ = requeue_job(
            job.public_id,
            expected_state_version=job.state_version,
            idempotency_key="retry-checkpoint-mismatch-0001",
            actor_id=self.user.pk,
        )

        self.assertEqual(
            receipt.mode, VaultJobRetryRequest.Mode.FRESH_SNAPSHOT
        )

    def test_missing_mutated_or_unsafe_snapshot_database_forces_fresh(self):
        for posture in (
            "missing",
            "mutated",
            "symlink",
            "internal_symlink",
            "fifo",
            "directory",
        ):
            with self.subTest(posture=posture):
                job = self._job()
                self._finalized_snapshot(
                    job,
                    database_posture=posture,
                )

                _, receipt, _ = requeue_job(
                    job.public_id,
                    expected_state_version=job.state_version,
                    idempotency_key=f"retry-database-{posture}-0001",
                    actor_id=self.user.pk,
                )

                self.assertEqual(
                    receipt.mode,
                    VaultJobRetryRequest.Mode.FRESH_SNAPSHOT,
                )

    def test_stale_finalized_workspace_cleanup_succeeds(self):
        job = self._job()
        snapshot, workspace = self._finalized_snapshot(
            job, trusted_fingerprint={}
        )

        requeue_job(
            job.public_id,
            expected_state_version=job.state_version,
            idempotency_key="retry-stale-finalized-cleanup-0001",
            actor_id=self.user.pk,
        )

        self.assertFalse(workspace.exists())
        snapshot.refresh_from_db()
        self.assertEqual(
            snapshot.cleanup_state, SourceSnapshot.CleanupState.COMPLETED
        )
        self.assertEqual(snapshot.workspace_path, "")

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
            "operation_retry",
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
        self.assertEqual(first.json()["reason_code"], "job_retry_queued")
        self.assertEqual(
            first.json()["data"]["retry_mode"], "fresh_snapshot"
        )
        self.assertIn(
            "retry_presentation", first.json()["data"]
        )
        self.assertFalse(first.json()["data"]["idempotent_replay"])
        self.assertTrue(second.json()["data"]["idempotent_replay"])
        self.assertEqual(
            first.json()["state_version"], second.json()["state_version"]
        )
        self.assertEqual(
            VaultAuditEvent.objects.filter(action="job_requeued").count(), 1
        )

    @override_settings(VAULT_ADMIN_MUTATIONS_ENABLED=True)
    def test_cleanup_failure_cannot_turn_committed_retry_into_503(self):
        job = self._job()
        _snapshot, workspace = self._failed_snapshot(job)
        self.client.force_login(self.user)
        with patch(
            "vaultops.services.snapshot.reclaim_snapshot_cleanup_intents",
            side_effect=RuntimeError("cleanup unavailable"),
        ):
            response = self.client.post(
                reverse("vaultops:job_retry", args=[job.public_id]),
                {
                    "idempotency_key": "retry-cleanup-503-0001",
                    "job_state_version": str(job.state_version),
                },
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 202, response.content)
        self.assertEqual(response.json()["reason_code"], "job_retry_queued")
        self.assertTrue(workspace.exists())
        job.refresh_from_db()
        self.assertEqual(job.status, VaultJob.Status.QUEUED)

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
        self._finalized_snapshot(job)
        barrier = threading.Barrier(2)

        def submit():
            close_old_connections()
            barrier.wait()
            try:
                _job, receipt, created = requeue_job(
                    job.public_id,
                    expected_state_version=job.state_version,
                    idempotency_key="retry-concurrent-0001",
                    actor_id=self.user.pk,
                    actor_name=self.user.get_username(),
                )
                return created, receipt.mode
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _item: submit(), range(2)))

        job.refresh_from_db()
        self.assertEqual(
            sorted(created for created, _mode in results),
            [False, True],
        )
        self.assertEqual(
            {mode for _created, mode in results},
            {VaultJobRetryRequest.Mode.CHECKPOINT_RESUME},
        )
        self.assertEqual(job.retry_count, 1)
        self.assertEqual(VaultJobRetryRequest.objects.count(), 1)
        self.assertEqual(
            VaultAuditEvent.objects.filter(action="job_requeued").count(), 1
        )

    def test_hard_kill_workspace_waits_for_grace_and_live_owner_recheck(self):
        job = self._job()
        snapshot, workspace = self._failed_snapshot(
            job, state=SourceSnapshot.State.COPYING
        )
        SourceMutationState.objects.create(
            deployment_id="test",
            barrier_state=SourceMutationState.BarrierState.ACTIVE,
            barrier_owner_job=job.public_id,
            active_mutations=1,
        )

        requeue_job(
            job.public_id,
            expected_state_version=job.state_version,
            idempotency_key="retry-hard-kill-0001",
            actor_id=self.user.pk,
        )

        self.assertTrue(workspace.exists())
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.cleanup_state, SourceSnapshot.CleanupState.PENDING)
        state = SourceMutationState.objects.get(deployment_id="test")
        self.assertEqual(state.barrier_state, SourceMutationState.BarrierState.ACTIVE)
        self.assertEqual(state.barrier_owner_job, job.public_id)

        snapshot.cleanup_not_before = timezone.now() - timezone.timedelta(seconds=1)
        snapshot.save(update_fields=["cleanup_not_before", "updated_at"])
        reclaim_snapshot_cleanup_intents(
            now=timezone.now() + timezone.timedelta(minutes=5)
        )
        self.assertTrue(workspace.exists())

        state.active_mutations = 0
        state.save(update_fields=["active_mutations", "updated_at"])
        reclaim_snapshot_cleanup_intents(
            now=timezone.now() + timezone.timedelta(minutes=10)
        )
        self.assertFalse(workspace.exists())
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.cleanup_state, SourceSnapshot.CleanupState.COMPLETED)
        state.refresh_from_db()
        self.assertEqual(state.barrier_state, SourceMutationState.BarrierState.OPEN)
        self.assertIsNone(state.barrier_owner_job)

    def test_cleanup_refuses_unowned_path_without_deleting_it(self):
        job = self._job()
        outside = Path(self.temporary.name).parent / "not-owned.incomplete"
        outside.mkdir(exist_ok=True)
        snapshot = SourceSnapshot.objects.create(
            job=job,
            deployment_id="test",
            state=SourceSnapshot.State.FAILED,
            workspace_path=str(outside),
            cleanup_state=SourceSnapshot.CleanupState.PENDING,
            cleanup_path=str(outside),
            cleanup_not_before=timezone.now(),
        )
        reclaim_snapshot_cleanup_intents()
        self.assertTrue(outside.exists())
        snapshot.refresh_from_db()
        self.assertEqual(
            snapshot.cleanup_state,
            SourceSnapshot.CleanupState.QUARANTINED,
        )
        self.assertEqual(
            snapshot.cleanup_error_code, "snapshot_cleanup_path_invalid"
        )
        outside.rmdir()

    def test_receipt_and_audit_failures_preserve_workspace_and_job_state(self):
        for target in (
            "vaultops.services.jobs.VaultJobRetryRequest.objects.create",
            "vaultops.services.jobs.append_event",
        ):
            with self.subTest(target=target):
                job = self._job()
                snapshot, workspace = self._failed_snapshot(job)
                with patch(target, side_effect=RuntimeError("injected failure")):
                    with self.assertRaisesRegex(RuntimeError, "injected failure"):
                        requeue_job(
                            job.public_id,
                            expected_state_version=job.state_version,
                            idempotency_key=f"retry-failure-{job.pk:04d}",
                            actor_id=self.user.pk,
                        )
                self.assertTrue(workspace.exists())
                job.refresh_from_db()
                snapshot.refresh_from_db()
                self.assertEqual(job.status, VaultJob.Status.RETRYABLE_FAILED)
                self.assertEqual(job.retry_count, 0)
                self.assertEqual(snapshot.cleanup_state, SourceSnapshot.CleanupState.NONE)

    def test_control_commit_failure_preserves_workspace(self):
        job = self._job()
        snapshot, workspace = self._failed_snapshot(job)
        with patch.object(
            connections["control"],
            "commit",
            side_effect=RuntimeError("commit failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "commit failed"):
                requeue_job(
                    job.public_id,
                    expected_state_version=job.state_version,
                    idempotency_key="retry-commit-failure-0001",
                    actor_id=self.user.pk,
                )
        self.assertTrue(workspace.exists())

    def test_live_claim_prevents_due_cleanup(self):
        job = self._job()
        snapshot, workspace = self._failed_snapshot(job)
        with transaction.atomic(using="control"):
            stage_snapshot_cleanup(job, reason="test")
        job.status = VaultJob.Status.RUNNING
        job.claim_token_hash = "a" * 64
        job.heartbeat_at = timezone.now()
        job.save(
            update_fields=[
                "status",
                "claim_token_hash",
                "heartbeat_at",
                "updated_at",
            ]
        )
        snapshot.refresh_from_db()
        snapshot.cleanup_not_before = timezone.now() - timezone.timedelta(seconds=1)
        snapshot.save(update_fields=["cleanup_not_before", "updated_at"])

        reclaim_snapshot_cleanup_intents(
            now=timezone.now() + timezone.timedelta(minutes=5)
        )

        self.assertTrue(workspace.exists())
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.cleanup_state, SourceSnapshot.CleanupState.PENDING)

    @override_settings(
        VAULT_SNAPSHOT_CLEANUP_MAX_ITEMS=1,
        VAULT_SNAPSHOT_CLEANUP_MAX_SCAN_ITEMS=8,
    )
    def test_live_oldest_intent_does_not_starve_later_cleanup(self):
        live_job = self._job()
        later_job = self._job()
        live, live_path = self._failed_snapshot(live_job)
        later, later_path = self._failed_snapshot(later_job)
        with transaction.atomic(using="control"):
            stage_snapshot_cleanup(live_job, reason="test")
            stage_snapshot_cleanup(later_job, reason="test")
        live_job.status = VaultJob.Status.RUNNING
        live_job.claim_token_hash = "b" * 64
        live_job.heartbeat_at = timezone.now()
        live_job.save(
            update_fields=[
                "status",
                "claim_token_hash",
                "heartbeat_at",
                "updated_at",
            ]
        )
        due = timezone.now() - timezone.timedelta(seconds=1)
        SourceSnapshot.objects.filter(pk__in=[live.pk, later.pk]).update(
            cleanup_not_before=due
        )
        observed = timezone.now()

        self.assertEqual(
            reclaim_snapshot_cleanup_intents(now=observed), 1
        )

        live.refresh_from_db()
        later.refresh_from_db()
        self.assertTrue(live_path.exists())
        self.assertFalse(later_path.exists())
        self.assertEqual(live.cleanup_state, SourceSnapshot.CleanupState.PENDING)
        self.assertGreater(live.cleanup_not_before, observed)
        self.assertEqual(
            later.cleanup_state, SourceSnapshot.CleanupState.COMPLETED
        )

    @override_settings(
        VAULT_SNAPSHOT_CLEANUP_MAX_ITEMS=1,
        VAULT_SNAPSHOT_CLEANUP_MAX_SCAN_ITEMS=8,
    )
    def test_invalid_oldest_intent_is_quarantined_without_starvation(self):
        invalid_job = self._job()
        outside = Path(self.temporary.name).parent / "invalid-oldest.incomplete"
        outside.mkdir(exist_ok=True)
        invalid = SourceSnapshot.objects.create(
            job=invalid_job,
            deployment_id="test",
            state=SourceSnapshot.State.FAILED,
            workspace_path=str(outside),
            cleanup_state=SourceSnapshot.CleanupState.PENDING,
            cleanup_path=str(outside),
            cleanup_not_before=timezone.now() - timezone.timedelta(seconds=1),
        )
        later_job = self._job()
        later, later_path = self._failed_snapshot(later_job)
        with transaction.atomic(using="control"):
            stage_snapshot_cleanup(later_job, reason="test")
        later.cleanup_not_before = timezone.now() - timezone.timedelta(seconds=1)
        later.save(update_fields=["cleanup_not_before", "updated_at"])

        self.assertEqual(reclaim_snapshot_cleanup_intents(), 1)

        invalid.refresh_from_db()
        later.refresh_from_db()
        self.assertTrue(outside.exists())
        self.assertEqual(
            invalid.cleanup_state,
            SourceSnapshot.CleanupState.QUARANTINED,
        )
        self.assertIsNone(invalid.cleanup_not_before)
        self.assertFalse(later_path.exists())
        self.assertEqual(
            later.cleanup_state, SourceSnapshot.CleanupState.COMPLETED
        )
        outside.rmdir()

    @override_settings(VAULT_SNAPSHOT_CLEANUP_MAX_ITEMS=1)
    def test_cleanup_batch_is_bounded_and_recorded_tombstone_resumes(self):
        first_job = self._job()
        second_job = self._job()
        first, first_path = self._failed_snapshot(first_job)
        second, second_path = self._failed_snapshot(second_job)
        with transaction.atomic(using="control"):
            stage_snapshot_cleanup(first_job, reason="test")
            stage_snapshot_cleanup(second_job, reason="test")
        first_path.rename(self.root / f".{first.public_id}.cleanup")
        first.cleanup_state = SourceSnapshot.CleanupState.RECLAIMING
        first.cleanup_path = str(self.root / f".{first.public_id}.cleanup")
        first.save(
            update_fields=["cleanup_state", "cleanup_path", "updated_at"]
        )

        reclaimed = reclaim_snapshot_cleanup_intents()

        self.assertEqual(reclaimed, 1)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.cleanup_state, SourceSnapshot.CleanupState.COMPLETED)
        self.assertEqual(second.cleanup_state, SourceSnapshot.CleanupState.PENDING)
        self.assertTrue(second_path.exists())

    @override_settings(VAULT_SNAPSHOT_CLEANUP_MAX_BYTES=1)
    def test_cleanup_byte_bound_preserves_resumable_tombstone(self):
        job = self._job()
        snapshot, workspace = self._failed_snapshot(job)
        with transaction.atomic(using="control"):
            stage_snapshot_cleanup(job, reason="test")

        self.assertEqual(reclaim_snapshot_cleanup_intents(), 0)

        snapshot.refresh_from_db()
        tombstone = self.root / f".{snapshot.public_id}.cleanup"
        self.assertFalse(workspace.exists())
        self.assertTrue(tombstone.exists())
        self.assertEqual(snapshot.cleanup_state, SourceSnapshot.CleanupState.FAILED)
        self.assertEqual(
            snapshot.cleanup_error_code,
            "snapshot_cleanup_byte_limit_exceeded",
        )
        self.assertGreater(snapshot.cleanup_not_before, timezone.now())

    def test_snapshot_primary_failure_is_retained_when_cleanup_fails(self):
        job = self._job()
        source = self.root / "source"
        source.mkdir()
        with (
            patch(
                "vaultops.services.snapshot._copy_tree",
                side_effect=SnapshotError("snapshot_searchable_embeddings_invalid"),
            ),
            patch(
                "vaultops.services.snapshot.reclaim_snapshot_cleanup_intents",
                side_effect=RuntimeError("cleanup failed"),
            ),
            self.assertRaisesRegex(
                SnapshotError, "snapshot_searchable_embeddings_invalid"
            ),
        ):
            create_consistent_snapshot(
                job,
                source_roots={"media": source},
                snapshot_root=self.root,
            )

        snapshot = SourceSnapshot.objects.get(job=job)
        step = VaultJobStep.objects.get(job=job, phase="snapshot")
        self.assertEqual(
            snapshot.safe_error_code,
            "snapshot_searchable_embeddings_invalid",
        )
        self.assertEqual(snapshot.state, SourceSnapshot.State.FAILED)
        self.assertEqual(step.status, VaultJobStep.Status.FAILED)
        self.assertEqual(
            step.checkpoint["safe_error_code"],
            "snapshot_searchable_embeddings_invalid",
        )
        self.assertEqual(snapshot.cleanup_state, SourceSnapshot.CleanupState.PENDING)

    def test_snapshot_primary_failure_is_not_masked_by_cleanup_staging(self):
        job = self._job()
        source = self.root / "source-staging"
        source.mkdir()
        with (
            patch(
                "vaultops.services.snapshot._copy_tree",
                side_effect=SnapshotError("snapshot_faiss_index_missing"),
            ),
            patch(
                "vaultops.services.snapshot.stage_snapshot_cleanup",
                side_effect=RuntimeError("control cleanup unavailable"),
            ),
            self.assertRaisesRegex(
                SnapshotError, "snapshot_faiss_index_missing"
            ),
        ):
            create_consistent_snapshot(
                job,
                source_roots={"media": source},
                snapshot_root=self.root,
            )

        snapshot = SourceSnapshot.objects.get(job=job)
        self.assertEqual(
            snapshot.safe_error_code, "snapshot_faiss_index_missing"
        )
        self.assertEqual(snapshot.cleanup_state, SourceSnapshot.CleanupState.FAILED)
        self.assertEqual(
            snapshot.cleanup_error_code, "snapshot_cleanup_staging_failed"
        )
        self.assertEqual(snapshot.cleanup_path, snapshot.workspace_path)

    @override_settings(VAULT_SNAPSHOT_CLEANUP_MAX_BYTES=5)
    def test_cleanup_partial_progress_debits_aggregate_byte_bound(self):
        first_job = self._job()
        second_job = self._job()
        first, first_path = self._failed_snapshot(first_job)
        second, second_path = self._failed_snapshot(second_job)
        for workspace in (first_path, second_path):
            (workspace / "partial.bin").unlink()
            (workspace / "a.bin").write_bytes(b"abc")
            (workspace / "b.bin").write_bytes(b"defg")
        with transaction.atomic(using="control"):
            stage_snapshot_cleanup(first_job, reason="test")
            stage_snapshot_cleanup(second_job, reason="test")

        self.assertEqual(reclaim_snapshot_cleanup_intents(), 0)

        first.refresh_from_db()
        second.refresh_from_db()
        first_tombstone = self.root / f".{first.public_id}.cleanup"
        second_tombstone = self.root / f".{second.public_id}.cleanup"
        self.assertFalse((first_tombstone / "a.bin").exists())
        self.assertTrue((first_tombstone / "b.bin").exists())
        self.assertTrue((second_tombstone / "a.bin").exists())
        self.assertTrue((second_tombstone / "b.bin").exists())
        self.assertEqual(first.cleanup_state, SourceSnapshot.CleanupState.FAILED)
        self.assertEqual(second.cleanup_state, SourceSnapshot.CleanupState.FAILED)
