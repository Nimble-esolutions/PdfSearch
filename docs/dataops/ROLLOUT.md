# Data Operations rollout

> Current evidence and exact pending gates are maintained in
> [STATUS-2026-08-02.md](../STATUS-2026-08-02.md). This page is the
> procedure; it is not evidence that every step below has completed.

## As-of 2026-08-02

Completed:

- v2 source bucket and dataset were used for a verified legacy snapshot.
- Clone/rebind created clone-legacy-20260802T085639Z-86288855 in the separate
  stage dataset with 416 objects and 1,093,501,777 bytes.
- Quarantine restore, migrations through 0027, bilingual OCR fallback, and
  document-scoped indexing completed for all 242 PDFs.
- The 2026 HTTPS route is live and web, maintenance, and Redis are healthy.
- Stage environment selectors now use stage_2026 for both restore and backup;
  activation remains disabled.

Still pending:

- Merge and certify the cryptography remediation PR #169, including the
  post-merge immutable image build and Trivy scan.
- Register/schedule the signed runtime activation and verify exact generation,
  manifest digest, and readiness evidence.
- Publish the first stage backup, then prove isolated round-trip recovery.
- Obtain the public-authentication exception approval before any public login
  or admin exposure.

The replacement is intentionally additive until the restore and reindex gates
are green. For the 2026 stage recovery rollout:

1. Save a redacted key inventory and a recoverable snapshot of both the app and
   control databases before changing Dokploy ENV.
2. Create and register only the new RustFS buckets
   `ai-sahakar-prod-flowdocs-artifact-vault-v2` and
   `ai-sahakar-stage-2026-flowdocs-artifact-vault`; leave the historical
   production bucket and pointer untouched.
3. Apply the complete `.env.dataops.example` contract as one Dokploy update;
   keep the clone control disabled until access probes pass. Do not paste root
   credentials into the application environment.
4. Deploy one immutable image digest to `https://2026.ai-sahakar.net` only and
   verify web/maintenance image and lifecycle environment parity.
5. Mount the legacy `prod_flowdocs` volume read-only in a disposable operator
   container, create the independent paired stage data/control backup, and
   publish a stable candidate with SQLite integrity, foreign-key, count, and
   remote-hash evidence. Promote only that verified candidate in the new v2
   dataset.
6. Enable the Advanced-only `clone/rebind` control, select the exact source
   generation, and type its confirmation phrase. Record both manifest digests,
   parent lineage, registration, and destination pointer evidence.
7. Restore the cloned stage generation into quarantine/new generation storage,
   run migrations and reconciliation, reindex to `1.0`, and run English and
   Marathi search, listing, source-link, and PDF-access checks. Activate only
   through the existing signed atomic runtime mechanism; failures leave the
   previous pointer and generation untouched.
8. Publish the first `stage_2026` backup and verify its receipt before changing
   `DATAOPS_BACKUP_MODE` to `scheduled`.
9. Restore that stage recovery point into separate disposable data/control
   volumes and compare manifests, lineage, checksums, database checks, index
   ratio, readiness, and representative searches.

The stage HTTPS route is reachable and its current environment has
PUBLIC_SEARCH_ENABLED=1, but this does not approve production-derived
authentication or the full login/admin surface. A request to expose production
password hashes and the full login/admin surface requires an explicit security
owner, monitoring, incident response, and rollback-authority record. Without
that approval the technical work stops after private data activation and
round-trip evidence; no DNS or production traffic is changed.

The old control-plane records are not migrated into Data Operations. Keep the
pre-cutover snapshot until the post-restore search and document-count checks are
accepted by the operator.

The quarantine restore primitive downloads a selected generation, verifies each
object SHA-256 against its manifest, and writes a receipt under an isolated
workspace. It never replaces the runtime data root or changes an authoritative
pointer; a separate activation step must consume that verified workspace after
operator review.
