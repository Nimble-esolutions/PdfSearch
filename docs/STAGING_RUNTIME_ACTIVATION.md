# Staging Runtime Activation

## Current stage state (2026-08-02)

The cloned legacy generation is quarantine-ready but not active. The current
stage route is healthy at the container level while /readyz remains 503 because
there is no signed active generation and no stage backup receipt. Keep both
activation flags at zero until the control database contains the prepared
generation, exact canonical manifest digest, validation evidence, and an
approved signed activation request.

The stage environment uses DATAOPS_RESTORE_PROFILE=stage_2026 and
DATAOPS_BACKUP_PROFILE=stage_2026. The legacy prod_flowdocs mount remains
read-only at /mnt/legacy and is never an activation target. See
[STATUS-2026-08-02.md](STATUS-2026-08-02.md) for the verified generation,
OCR/indexing evidence, and remaining gates.

This runbook covers the staging-only runtime cutover protocol introduced by the
vault control plane. Production activation is hard-disabled in Django settings,
the coordinator, the legacy activation helper, and the process supervisor.

## Safety model

- The stable control database and signed protocol documents live under
  `DATA_CONTROL_ROOT`; they are not switched with application data.
- A restore prepares a generation under `RUNTIME_GENERATIONS_ROOT` and freezes
  it before it becomes activation-ready.
- The coordinator verifies the workspace, migration rehearsal, image/release
  identity, recovery login, and English/Marathi smoke-query file before writing
  a signed activation intent.
- The maintenance supervisor stops new work and writes a signed quiescence
  acknowledgement.
- The web supervisor freezes the old runtime, atomically changes the signed
  active pointer, thaws the target for normal Django writes, restarts Gunicorn,
  and verifies readiness.
- A failed verification freezes the target, restores and thaws the previous
  runtime, restarts Gunicorn, and verifies rollback readiness.
- Static files continue to come from the image. Restored static files remain
  custody evidence and are never activated as executable assets.

The runtime pointer is authoritative. Control-database lifecycle fields are
projections and cannot switch a runtime.

## Required configuration

Keep activation disabled while the control volume and bootstrap runtime are
being prepared:

```env
APP_ENV=staging
DATA_CONTROL_ROOT=/app/data-control
CONTROL_DB_PATH=/app/data-control/control.sqlite3
RUNTIME_GENERATIONS_ROOT=/app/data/runtime-generations

STAGING_RUNTIME_ACTIVATION_ENABLED=0
MAINTENANCE_CANDIDATE_PREPARATION_ENABLED=0
MAINTENANCE_CANDIDATE_WRITER_MODE=0
STAGING_INITIAL_ACTIVATION_ENABLED=0
STAGING_ACTIVATION_APPLY_MODE=pending
ACTIVATION_INTENT_SIGNING_KEY=<at-least-32-random-characters>
ACTIVATION_SMOKE_QUERIES_FILE=/app/data-control/config/activation-smoke-queries.json
ACTIVATION_RECOVERY_SUPERADMIN_USERNAME=<staging-recovery-user>
ACTIVATION_RECOVERY_SUPERADMIN_PASSWORD=<staging-only-secret>
```

Run activation management commands as the application account:

```bash
docker compose exec -T --user appuser web \
  python manage.py <activation-command>
```

Do not use an unqualified `docker compose exec` for activation. Docker starts
such an exec as root in this image; signed intent directories created by root
are intentionally private and therefore inaccessible to the appuser runtime
supervisor.

The signing key and recovery password are secrets. Deploy them through the
platform secret store. Do not put their values in Git, logs, audit evidence, or
support bundles.

Both web and maintenance services must receive the same deployment ID, control
root, runtime root, signing key, smoke-query path, and recovery credential.
Both services must mount the same durable data and control volumes.

Enable `MAINTENANCE_CANDIDATE_WRITER_MODE=1` on exactly one maintenance worker
authorized to materialize candidates. Web and peer maintenance processes must
keep it disabled so only one process advances candidate checkpoints and builds
the final folder indexes.

### Active-runtime mutability boundary

Activation-ready and previous runtimes remain frozen and byte-identical.
Current architecture thaws only the signed target after cutover because Django
still stores application state, sessions, and authentication timestamps in the
active SQLite database. A rollback therefore restores the prior content and
schema identity, but subsequent reauthentication may change `django_session`
rows and `CustomUser.last_login`. Media, manifests, document/chunk/embedding
rows, and index bytes must remain unchanged.

Separating operational/session writes from immutable content custody is
residual architecture debt. Until that storage split exists, verification must
compare database schema and table-level content, explicitly allow only those
operational fields, and fail closed on any other drift.

The smoke-query file must contain at least one English and one Marathi query:

```json
{
  "queries": [
    {
      "locale": "en",
      "query": "approved English staging smoke query",
      "expect_references": true
    },
    {
      "locale": "mr",
      "query": "मंजूर मराठी स्टेजिंग चाचणी प्रश्न",
      "expect_references": true
    }
  ]
}
```

`allow_empty` is intended only for disposable test fixtures. Do not use it for
a staging activation acceptance probe.

## Bootstrap

1. Back up the application and control SQLite databases.
2. Confirm `/app/data-control` is durable, shared, writable by the application
   user, and not inside a runtime generation.
3. Restore and validate the current staging dataset through the vault restore
   pipeline. The resulting workspace must be `activation_ready`.
4. Confirm the prepared runtime contains:
   `db.sqlite3`, `media/`, `pdf_cache/`, `faiss_indexes/`, `chroma_db/`, and
   `runtime-evidence.json`.
5. Create the initial signed `runtime/active.json` through the same
   `vaultops.runtime_control.build_runtime_pointer()` implementation used by the
   coordinator. Do not hand-edit a pointer.
6. Start both services with activation still disabled and verify `/livez` and
   `/readyz`.
7. Enable `STAGING_RUNTIME_ACTIVATION_ENABLED=1` for both services and restart
   them together. Startup rejects a missing, malformed, wrongly signed, or
   cross-deployment pointer.
8. Enable `MAINTENANCE_CANDIDATE_PREPARATION_ENABLED=1` only when this staging
   environment should import validated local-maintenance candidates. This
   separate gate does not publish or activate a candidate.

When activation mode is enabled, startup also rejects legacy import, JSON
migration, and superuser-bootstrap switches because those would mutate a
prepared runtime outside the supervisor protocol.

## Schedule and observe a cutover

Only a confirmed superadmin workflow may call
`vaultops.services.activation.schedule_activation()`.

After scheduling, inspect safe state only:

- the control database `ActivationIntent` state and checkpoint;
- signed files under `activation/intents`, `activation/acks`, and
  `activation/results`;
- `/readyz` runtime generation and manifest digest;
- the latest `RuntimePointerObservation`;
- the append-only vault audit event.

Never expose the signing key, password hashes, recovery password, raw exception
strings, or Redis lease tokens while diagnosing activation.

A committed cutover must prove all of the following:

- the active pointer resolves inside the configured runtime root;
- pointer, intent, manifest, and deployment identities agree;
- SQLite integrity and foreign-key checks pass;
- no Django migrations are pending;
- the configured recovery account authenticates and is an active superadmin;
- every PDF path resolves inside the active media root and the file exists;
- FAISS metadata and counts are coherent;
- both approved search probes pass;
- `/livez` and `/readyz` report the target generation.

## Failure and restart behavior

If target verification fails, the result is `rolled_back` only after the
previous runtime has restarted and passed readiness. If rollback verification
also fails, the result is `rollback_failed`; treat that as a critical incident.

If the web supervisor restarts after the pointer changes but before a committed
result exists, it conservatively restores the signed previous pointer and
verifies the previous runtime. It never infers success from the newest
generation.

Signed results are durable. If result projection into the control database
temporarily fails, a later `reconcile_activation_result --intent-id <uuid>` can
replay it idempotently. Do not rewrite the result file.

## Production guarantee

`STAGING_RUNTIME_ACTIVATION_ENABLED=1` with `APP_ENV=production` fails startup
with `production_activation_disabled`. The supervisor also rejects a production
intent before stopping a child process or touching a runtime pointer.

Remote rollback remains a separate, audited CAS promotion. Runtime rollback
never edits an immutable vault generation or silently changes the remote
authoritative pointer.
