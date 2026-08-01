# Data Operations rollout

The replacement is intentionally additive until the restore and reindex gates
are green. For the stage rollout:

1. Save a redacted key inventory and a recoverable snapshot of both the app and
   control databases before changing Dokploy ENV.
2. Apply the complete `.env.dataops.example` contract as one Dokploy update;
   do not paste individual keys in a different order or mix legacy `VAULT_*`
   aliases with the new contract.
3. Deploy an immutable image digest to `https://2026.ai-sahakar.net` only.
4. Verify the running image digest, migrations, worker health, public readiness,
   Data Operations status, and S3 profile observations.
5. Inspect the selected v1 recovery point in quarantine. Repack to v2 only after
   every object hash, database integrity check, migration rehearsal, sanitization
   check, and search smoke check passes.
6. Staging may auto-activate after the complete gate. Production activation stays
   confirmation-gated. Run bounded reindexing and record the receipt.

The old control-plane records are not migrated into Data Operations. Keep the
pre-cutover snapshot until the post-restore search and document-count checks are
accepted by the operator.

The quarantine restore primitive downloads a selected generation, verifies each
object SHA-256 against its manifest, and writes a receipt under an isolated
workspace. It never replaces the runtime data root or changes an authoritative
pointer; a separate activation step must consume that verified workspace after
operator review.
