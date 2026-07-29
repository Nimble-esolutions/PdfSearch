# Plan 003: Complete the Vault recovery lifecycle and deployment proof

> **Executor instructions**: The active lifecycle implementation is the
> `vaultops` control plane. Do not reconnect the retired generation actions in
> `core.maintenance`, and do not recreate custody or activation logic in views
> or templates.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: HIGH
- **Depends on**: Plan 006 gate
- **Category**: operations / data lifecycle
- **Planned at**: commit `f742b59`, 2026-07-26
- **Reconciled at**: commits `c545312`, `6df613c`, `43e1cca`,
  2026-07-29
- **Roadmap status**: RECONCILE

## Drift check

```bash
git diff --stat c545312..HEAD -- \
  flowdocs/vaultops flowdocs/core/management/commands/run_maintenance_jobs.py \
  flowdocs/core/management/commands/activate_worker.py \
  flowdocs/core/management/commands/activation_supervisor.py \
  docker-compose.yml docker-compose.integration.yml scripts/ci \
  integration_tests docs plans
```

Any required production restore, publication, pointer, or activation change is
a STOP condition requiring separate operator approval.

## Implemented baseline

The historical Plan 003 audit correctly identified that the old
`core.maintenance` generation path could not prove runtime activation. That
path is now retired from the operator interface. The current baseline is:

- `flowdocs/vaultops/` owns independent Vault, runtime, and local-presence
  projections in the durable control database.
- Profiles resolve only server-approved credential aliases and retain bounded,
  redacted technical evidence.
- Durable, idempotent `VaultJob` records use claim-token hashes, fencing epochs,
  heartbeats, stale recovery, typed failure codes, and append-only audit events.
- Publication creates immutable dataset-scoped candidates. Promotion performs
  a confirmed conditional authoritative-pointer update and does not claim that
  runtime bytes changed.
- The operator restore job verifies authoritative inventory, downloads and
  validates objects, checks compatibility, sanitizes when required, rehearses
  migrations, and produces an `activation_ready` workspace.
- Runtime activation and rollback are separate, explicitly confirmed actions
  coordinated through signed intents, readiness evidence, an activation
  supervisor, and crash recovery. A projected Vault state is never treated as
  proof of runtime activation.
- Durable mutation epochs and snapshot barriers fence source changes. The sync
  scheduler coalesces work by mutation epoch and publication completes only
  after authoritative-pointer publication.
- The disposable integration stack now initializes MinIO, shares its network
  with Redis and the lifecycle runner, uses explicit entrypoints, requires
  immutable dependency images, cleans up automatically, and exercises
  publication, promotion, activation-ready restore, fencing, and process-death
  recovery.
- The disposable maintenance lifecycle carries one exact
  `(generation_id, manifest_digest)` through publication, authoritative
  promotion, object verification, restore with migration rehearsal, signed
  dual-supervisor activation, fresh Gunicorn readiness, English/Marathi
  searches, and final Vault/control evidence reconciliation. CI runs this path
  on every pull request and `dev` release.
- The Workbench separates ordinary **Documents & Search** maintenance from
  advanced **Vault & Recovery**, uses human English/Marathi guidance, and keeps
  machine evidence collapsed and role-bounded.
- Both entrypoints consume `startup-*` restore policy as a DB-free, fail-closed
  posture check before imports, seeds, backups, migrations, queues, remote
  Vault calls, or activation. They preserve an existing non-empty database and
  refuse absent/zero-byte database creation with a stable reason.
- Production Compose explicitly gives web and maintenance the same required
  Vault, restore, scheduler, sync-policy, pinned-generation, and immutable
  release inputs; CI rejects parity drift.

Legacy functions such as `core.maintenance.stage_generation()` remain only for
compatibility and historical tests. They are not the operator recovery
contract and must not be used to assess current lifecycle completeness.

## Residual gaps

1. Two deployment drills remain unrecorded: preservation of an accumulated
   named volume across redeploy, and restore of a selected generation into a
   genuinely fresh disposable volume with no host-only state.
2. Production RustFS readiness remains unproved until an operator-approved,
   non-destructive clean-volume drill records generation identity, manifest
   digest, runtime pointer, readiness results, and rollback evidence.

## Reconciliation steps

1. **Complete:** CI enforces explicit web/maintenance environment parity for
   Vault, restore, scheduling, sync policy, pinned generation, and immutable
   release identity.
2. **Complete:** both entrypoints now enforce the chosen fail-closed
   `startup-*` contract before database mutation. Automatic restore remains
   deliberately outside startup.
3. **Complete:** the disposable maintenance lifecycle now selects the same
   published generation, restores it, activates it through the real
   supervisors, and verifies generation-plus-manifest runtime readiness and
   bilingual search.
4. Exercise disabled, missing configuration, unreachable, forbidden, missing
   bucket, stale manifest, healthy, duplicate request, cancellation, and stale
   ownership states through the active `vaultops` path.
5. Run and record the accumulated-volume redeploy drill.
6. Run and record the fresh-volume restore drill locally, then repeat it
   against production RustFS only with explicit operator authorization.
7. Reconcile all lifecycle documentation and mark this plan `DONE` only after
   both deployment drills are complete.

## Verification

```bash
python manage.py test vaultops core.tests_maintenance_contract
python manage.py check
python manage.py makemigrations --check --dry-run
python scripts/ci/assert_compose_env_parity.py
bash scripts/ci/run_vault_integration.sh
git diff --check
```

Use the repository release gate for image or entrypoint changes. Local MinIO
evidence validates the mechanism; it is not production RustFS evidence.

## Done criteria

- The web and maintenance services have enforced, value-redacted environment
  parity for every lifecycle-critical key.
- A sync-created generation follows the active operator path through
  activation-ready restore and separately confirmed byte-level activation.
- `active` or equivalent runtime-ready language appears only after the runtime
  pointer switches and post-activation checks pass.
- `startup-*` restores a fresh volume or fails closed with actionable human
  guidance; it never silently starts empty.
- Scheduled mode publishes once for a real durable mutation epoch and does not
  republish unchanged data.
- Duplicate actions do not duplicate work; retries, cancellation, stale
  recovery, role checks, CSRF checks, and audit evidence remain observable.
- The integration Compose gate runs without manual entrypoint, network, bucket,
  or cleanup workarounds.
- Both accumulated-volume and fresh-volume drills pass and retain the exact
  generation, manifest, runtime pointer, readiness, and rollback evidence.

## STOP conditions

Stop if a check requires exposing credentials; if generation identity differs
between registration, authoritative pointer, manifest, workspace, runtime
pointer, or activation journal; if rollback cannot name the previous
generation; or if production data or authority would be mutated without
explicit operator approval.
