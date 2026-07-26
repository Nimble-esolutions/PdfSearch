import json
import uuid

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.lease import acquire_lease, release_lease
from core.models import ArtifactGeneration as LegacyGeneration
from core.models import CustomUser
from vaultops.models import (
    ArtifactGeneration,
    ConfirmationChallenge,
    GarbageCollectionPlan,
    RetentionHold,
    VaultConnectionProfile,
    VaultDatasetProjection,
    VaultAuditEvent,
    VaultJob,
)


@override_settings(
    VAULT_ADMIN_MUTATIONS_ENABLED=True,
    VAULT_SYNC_ENABLED=True,
    VAULT_RESTORE_ENABLED=True,
)
class VaultWorkbenchTests(TestCase):
    databases = {"default", "control"}

    def setUp(self):
        self.superadmin = CustomUser.objects.create_user(
            username="vault-superadmin",
            password="test-password",
            role="superadmin",
            is_staff=True,
            is_superuser=True,
        )
        self.admin = CustomUser.objects.create_user(
            username="vault-admin",
            password="test-password",
            role="admin",
            is_staff=True,
        )
        identity = settings.ENV_IDENTITY
        self.profile = VaultConnectionProfile.objects.create(
            key=settings.VAULT_DEFAULT_PROFILE,
            display_name="Locked environment vault",
            source=VaultConnectionProfile.Source.ENVIRONMENT,
            enabled=True,
            read_only=False,
            environment_locked=True,
            endpoint_origin="https://vault.example",
            bucket="artifacts",
            region="test",
            dataset_id=identity.dataset_id,
            production_source_id=identity.production_source_id,
            credential_alias="environment:ARTIFACT_VAULT",
            fingerprint="f" * 64,
        )
        VaultDatasetProjection.objects.create(
            profile=self.profile,
            dataset_id=self.profile.dataset_id,
            inventory_state="verified",
            authoritative_generation_id="generation-authoritative",
            pointer_digest="a" * 64,
        )
        self.candidate = ArtifactGeneration.objects.create(
            profile=self.profile,
            dataset_id=self.profile.dataset_id,
            generation_id="generation-candidate",
            manifest_digest="b" * 64,
            vault_state=ArtifactGeneration.VaultState.CANDIDATE,
            runtime_state=ArtifactGeneration.RuntimeState.INACTIVE,
            local_presence=ArtifactGeneration.LocalPresence.ABSENT,
            manifest={"files": []},
        )
        self.client.force_login(self.superadmin)

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def test_superadmin_workbench_is_server_rendered_and_no_js_required(self):
        response = self.client.get(reverse("operations_panel"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Vault Operations Workbench")
        self.assertContains(response, "Authority comparison")
        self.assertContains(response, "vault-workbench.css")
        self.assertContains(response, "<noscript>", html=False)
        self.assertNotContains(response, "cdn.jsdelivr.net")
        self.assertContains(response, "vendor/bootstrap/5.3.0")
        sync_response = self.client.get(
            reverse("operations_panel"), {"section": "sync"}
        )
        self.assertContains(sync_response, "Queue publish-only sync")

    def test_non_superadmin_is_forbidden(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("operations_panel"))
        self.assertEqual(response.status_code, 403)

    def test_marathi_workbench_uses_reviewed_operations_language(self):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = "mr"
        response = self.client.get(reverse("operations_panel"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "तिजोरी संचालन कार्यपटल")
        self.assertContains(response, "अधिकृत स्थिती तुलना")
        self.assertNotContains(response, "Vault Operations Workbench")
        retention = self.client.get(
            reverse("operations_panel"), {"section": "retention"}
        )
        self.assertContains(retention, "पिढी निवृत्ती")
        self.assertContains(retention, "कचरा संकलन योजना")

    def test_state_api_is_redacted_and_uses_contract_envelope(self):
        lease = acquire_lease(
            self.profile.dataset_id,
            "sensitive-instance-name",
            ttl_seconds=30,
        )
        try:
            response = self.client.get(reverse("vaultops:state"))
        finally:
            release_lease(lease)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("status", payload)
        self.assertIn("reason_code", payload)
        self.assertIn("observed_at", payload)
        self.assertIn("correlation_id", payload)
        self.assertIn("state_version", payload)
        serialized = json.dumps(payload)
        self.assertNotIn(lease.owner_token, serialized)
        self.assertNotIn("sensitive-instance-name", serialized)
        self.assertEqual(payload["data"]["lease"]["state"], "held")

    def test_diagnostics_export_is_redacted_and_aggregated(self):
        object_key = "datasets/private/objects/pdf/" + "7" * 64
        self.candidate.manifest = {
            "files": [{"object_key": object_key, "bytes": 12}]
        }
        self.candidate.save(update_fields=["manifest", "updated_at"])

        response = self.client.get(reverse("vaultops:diagnostics"))
        payload = response.json()
        serialized = json.dumps(payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["data"]["counts"]["generations"], 1)
        self.assertFalse(
            payload["data"]["retention"]["gc_execution_enabled"]
        )
        self.assertNotIn(object_key, serialized)
        self.assertNotIn("credential_alias", serialized)

    def test_state_version_is_stable_without_authority_changes(self):
        first = self.client.get(reverse("vaultops:state")).json()
        second = self.client.get(reverse("vaultops:state")).json()
        self.assertEqual(first["state_version"], second["state_version"])

    def test_fresh_rendered_state_version_passes_mutation_guard(self):
        page = self.client.get(
            reverse("operations_panel"), {"section": "sync"}
        )
        response = self.client.post(
            reverse("vaultops:sync_run"),
            {
                "idempotency_key": str(uuid.uuid4()),
                "state_version": page.context["state"]["state_version"],
            },
        )
        self.assertEqual(response.status_code, 303)

    def test_typed_promotion_confirmation_is_one_use(self):
        issue = self.client.post(
            reverse("vaultops:confirmation_issue"),
            {
                "idempotency_key": str(uuid.uuid4()),
                "action": "promote_generation",
                "target": self.candidate.generation_id,
            },
        )
        self.assertEqual(issue.status_code, 200)
        challenge = issue.context["challenge"]
        phrase = issue.context["phrase"]
        submit = self.client.post(
            reverse(
                "vaultops:promote_generation",
                kwargs={"generation_id": self.candidate.generation_id},
            ),
            {
                "idempotency_key": str(uuid.uuid4()),
                "challenge_id": str(challenge.public_id),
                "confirmation_phrase": phrase,
            },
        )
        self.assertEqual(submit.status_code, 303)
        job = VaultJob.objects.get(operation="promote_generation")
        self.assertTrue(
            job.progress["confirmation"].startswith("operator:")
        )
        audit = VaultAuditEvent.objects.get(
            action="job_queued", job_public_id=job.public_id
        )
        self.assertEqual(
            audit.confirmation_digest,
            job.progress["confirmation"].removeprefix("operator:"),
        )
        replay = self.client.post(
            reverse(
                "vaultops:promote_generation",
                kwargs={"generation_id": self.candidate.generation_id},
            ),
            {
                "idempotency_key": str(uuid.uuid4()),
                "challenge_id": str(challenge.public_id),
                "confirmation_phrase": phrase,
            },
        )
        self.assertEqual(replay.status_code, 303)
        self.assertEqual(
            VaultJob.objects.filter(
                operation="promote_generation"
            ).count(),
            1,
        )

    def test_typed_retirement_is_reversible_and_never_claims_deletion(self):
        page = self.client.get(
            reverse("operations_panel"), {"section": "retention"}
        )
        self.assertContains(page, "Retirement removes promotion eligibility")
        self.assertContains(page, "Dry-run only")
        issue = self.client.post(
            reverse("vaultops:confirmation_issue"),
            {
                "idempotency_key": str(uuid.uuid4()),
                "action": "retire_generation",
                "target": self.candidate.generation_id,
            },
        )
        retirement = self.client.post(
            reverse(
                "vaultops:retire_generation",
                kwargs={"generation_id": self.candidate.generation_id},
            ),
            {
                "idempotency_key": str(uuid.uuid4()),
                "challenge_id": str(issue.context["challenge"].public_id),
                "confirmation_phrase": issue.context["phrase"],
            },
        )
        self.assertEqual(retirement.status_code, 303)
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.vault_state, "retired")

        issue = self.client.post(
            reverse("vaultops:confirmation_issue"),
            {
                "idempotency_key": str(uuid.uuid4()),
                "action": "unretire_generation",
                "target": self.candidate.generation_id,
            },
        )
        unretirement = self.client.post(
            reverse(
                "vaultops:unretire_generation",
                kwargs={"generation_id": self.candidate.generation_id},
            ),
            {
                "idempotency_key": str(uuid.uuid4()),
                "challenge_id": str(issue.context["challenge"].public_id),
                "confirmation_phrase": issue.context["phrase"],
            },
        )
        self.assertEqual(unretirement.status_code, 303)
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.vault_state, "candidate")

    def test_retention_hold_create_and_release_use_fresh_state(self):
        state = self.client.get(reverse("vaultops:state")).json()
        idempotency_key = str(uuid.uuid4())
        created = self.client.post(
            reverse(
                "vaultops:retention_hold_create",
                kwargs={"generation_id": self.candidate.generation_id},
            ),
            {
                "idempotency_key": idempotency_key,
                "state_version": state["state_version"],
                "reason_code": "incident",
                "owner_reference": "INC-42",
            },
        )
        self.assertEqual(created.status_code, 303)
        hold = RetentionHold.objects.get()
        self.assertIsNone(hold.released_at)

        replayed = self.client.post(
            reverse(
                "vaultops:retention_hold_create",
                kwargs={"generation_id": self.candidate.generation_id},
            ),
            {
                "idempotency_key": idempotency_key,
                "state_version": state["state_version"],
                "reason_code": "different-replay-value",
                "owner_reference": "INC-99",
            },
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(replayed.status_code, 202, replayed.content)
        self.assertEqual(RetentionHold.objects.count(), 1)
        self.assertEqual(
            replayed.json()["data"]["hold_id"],
            hold.pk,
        )
        hold.refresh_from_db()
        self.assertEqual(hold.owner_reference, "INC-42")

        state = self.client.get(reverse("vaultops:state")).json()
        released = self.client.post(
            reverse(
                "vaultops:retention_hold_release",
                kwargs={"hold_id": hold.pk},
            ),
            {
                "idempotency_key": str(uuid.uuid4()),
                "state_version": state["state_version"],
            },
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(released.status_code, 202, released.content)
        hold.refresh_from_db()
        self.assertIsNotNone(hold.released_at)

    def test_gc_execute_endpoint_is_disabled_even_for_ready_plan(self):
        plan = GarbageCollectionPlan.objects.create(
            profile=self.profile,
            dataset_id=self.profile.dataset_id,
            inventory_version="i" * 64,
            pointer_version="p" * 64,
            candidates=[
                {
                    "generation_id": "generation-retired",
                    "exclusive_object_keys": ["private/object/key"],
                }
            ],
            estimates={
                "deletion_enabled": False,
                "exclusive_objects": 1,
                "exclusive_bytes": 10,
            },
            plan_digest="g" * 64,
            state=GarbageCollectionPlan.State.READY,
            expires_at=timezone.now(),
        )
        listing = self.client.get(reverse("vaultops:gc_plans"))
        self.assertNotContains(listing, "private/object/key")
        page = self.client.get(
            reverse("operations_panel"), {"section": "retention"}
        )
        self.assertContains(page, "GC execution disabled")
        state = self.client.get(reverse("vaultops:state")).json()
        response = self.client.post(
            reverse(
                "vaultops:gc_plan_execute",
                kwargs={"plan_id": plan.public_id},
            ),
            data=json.dumps(
                {
                    "idempotency_key": str(uuid.uuid4()),
                    "state_version": state["state_version"],
                }
            ),
            content_type="application/json",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["reason_code"], "gc_execution_disabled"
        )

    def test_confirmation_is_rejected_after_observed_state_changes(self):
        issue = self.client.post(
            reverse("vaultops:confirmation_issue"),
            {
                "idempotency_key": str(uuid.uuid4()),
                "action": "promote_generation",
                "target": self.candidate.generation_id,
            },
        )
        challenge = issue.context["challenge"]
        phrase = issue.context["phrase"]
        self.candidate.local_presence = (
            ArtifactGeneration.LocalPresence.QUARANTINED
        )
        self.candidate.save(update_fields=["local_presence", "updated_at"])
        response = self.client.post(
            reverse(
                "vaultops:promote_generation",
                kwargs={"generation_id": self.candidate.generation_id},
            ),
            {
                "idempotency_key": str(uuid.uuid4()),
                "challenge_id": str(challenge.public_id),
                "confirmation_phrase": phrase,
            },
        )
        self.assertEqual(response.status_code, 303)
        self.assertFalse(
            VaultJob.objects.filter(
                operation="promote_generation"
            ).exists()
        )

    def test_legacy_generation_actions_do_not_relabel_or_delete(self):
        generation = LegacyGeneration.objects.create(
            generation_id="legacy-generation",
            status="validated",
        )
        for route in (
            "promote_generation",
            "rollback_generation",
            "purge_generation",
        ):
            response = self.client.post(
                reverse(route, kwargs={"generation_id": generation.generation_id})
            )
            self.assertEqual(response.status_code, 302)
            generation.refresh_from_db()
            self.assertEqual(generation.status, "validated")
        response = self.client.post(reverse("purge_expired_generations"))
        self.assertEqual(response.status_code, 302)
        generation.refresh_from_db()
        self.assertEqual(generation.status, "validated")

    def test_legacy_vault_page_redirects_to_generations(self):
        response = self.client.get(reverse("vault_operations"))
        self.assertRedirects(
            response,
            f"{reverse('operations_panel')}?section=generations",
            fetch_redirect_response=False,
        )

    def test_mutation_rejects_missing_idempotency_key(self):
        response = self.client.post(
            reverse("vaultops:restore_start"),
            {
                "profile_key": self.profile.key,
                "state_version": "not-current",
            },
        )
        self.assertEqual(response.status_code, 303)
        self.assertFalse(
            VaultJob.objects.filter(
                operation="restore_generation"
            ).exists()
        )

    def test_json_mutation_error_uses_contract_envelope(self):
        response = self.client.post(
            reverse("vaultops:restore_start"),
            data=json.dumps({"profile_key": self.profile.key}),
            content_type="application/json",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(
            payload["reason_code"], "idempotency_key_required"
        )
        self.assertIn("correlation_id", payload)

    @override_settings(VAULT_ADMIN_MUTATIONS_ENABLED=False)
    def test_server_rejects_post_when_admin_mutations_are_disabled(self):
        response = self.client.post(
            reverse("vaultops:confirmation_issue"),
            data=json.dumps(
                {
                    "idempotency_key": str(uuid.uuid4()),
                    "action": "promote_generation",
                    "target": self.candidate.generation_id,
                }
            ),
            content_type="application/json",
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["reason_code"],
            "vault_admin_mutations_disabled",
        )
        self.assertFalse(ConfirmationChallenge.objects.exists())

    def test_mutation_rate_limit_is_actor_and_route_scoped(self):
        url = reverse("vaultops:confirmation_issue")
        for attempt in range(30):
            response = self.client.post(
                url,
                {
                    "idempotency_key": f"attempt-{attempt:03d}",
                    "action": "unsupported",
                    "target": "target",
                },
                HTTP_ACCEPT="application/json",
            )
            self.assertEqual(response.status_code, 400)
        blocked = self.client.post(
            url,
            {
                "idempotency_key": "attempt-030",
                "action": "unsupported",
                "target": "target",
            },
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.json()["reason_code"], "rate_limited")
