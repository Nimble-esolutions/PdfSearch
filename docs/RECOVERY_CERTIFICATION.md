Status: Active, operator-controlled
Audience: Recovery, Operator
Owner: FlowDocs maintainers
Last verified: 2026-07-30
Canonical source: docs/RECOVERY_CERTIFICATION.md

# Isolated Recovery Certification

This procedure proves recovery mechanics without mounting or changing the live
application, control, Redis, or legacy volumes. It is a certification drill,
not a production cutover.

## Required authorization and evidence

Record the immutable application image, selected Vault generation and manifest
digest, source backup references, recovery-point age, maintenance window, and
the unique certification run ID. Never put credentials or document contents in
the evidence directory.

The drill requires an existing Docker network that reaches the approved Vault.
That network must not provide public ingress. The application is exposed only
on an unused `127.0.0.1` port; its other network is Docker-internal.

## Fresh-volume drill

Set the approved environment through the deployment secret provider, including
the Vault endpoint, bucket, region, credential alias material, dataset identity,
activation signing key and recovery-superadmin credentials. Generate unique,
ephemeral values for this disposable target and inject them through the
deployment secret provider as
`CERT_ACTIVATION_INTENT_SIGNING_KEY`,
`CERT_ACTIVATION_RECOVERY_SUPERADMIN_USERNAME`, and
`CERT_ACTIVATION_RECOVERY_SUPERADMIN_PASSWORD`. They must not be written to the
evidence directory.

Startup account creation is always disabled (`CREATE_SUPERUSER=0`). If the
restored database does not contain an authorized operator, create one explicitly
inside the disposable target with the normal Django administration command;
never enable the startup bootstrap switch.

The activation phase enables both ordinary staging activation and the narrower
initial-activation gate. The latter is still rejected unless the disposable
target has no active or previous runtime pointer and no generation projected as
active.

Then run:

```bash
export PDFSEARCH_IMAGE='<repository>@sha256:<digest>'
export CERT_VAULT_NETWORK='<approved-vault-only-network>'
export CERT_WEB_PORT='<unused-localhost-port>'
export CERT_RUN_ID='<unique-lowercase-run-id>'
bash scripts/ops/run_recovery_certification.sh fresh start
```

The wrapper creates paired empty data and control volumes, an empty legacy
substitute, isolated Redis storage, and a private network. It renders the final
Compose model and stops if the mounts, networks, image identity, or localhost
binding differ from the certification contract. Redis, web, and maintenance
start sequentially so the two application roles cannot race SQLite migrations.
Runtime activation remains disabled throughout this pre-restore phase.

Use the localhost Workbench as an authorized superadmin:

1. probe the environment-backed Vault profile;
2. verify the authoritative inventory;
3. select the recorded generation and confirm its manifest digest;
4. prepare the restore through download, checksum validation, sanitization,
   compatibility checking, and migration rehearsal;
5. after restore preparation and compatibility checks pass, enable activation
   as a distinct phase:

   ```bash
   bash scripts/ops/run_recovery_certification.sh fresh activate
   ```

6. separately confirm signed activation;
7. require the exact generation and manifest in the active pointer and signed
   result;
8. test English and Marathi search, document/source access, login, PDF/FAISS
   counts, `/livez`, `/readyz`, and `/health/data/`.

Do not promote, retire, garbage-collect, or publish a generation during this
drill. The wrapper disables writer and scheduler roles.

## Accumulated-volume redeploy preflight

First create and record independent backups of both the application data and
control volumes. Quiesce every container that mounts the source pair. The
wrapper refuses to copy a source mounted by a running container.

```bash
export CERT_SOURCE_DATA_VOLUME='<quiesced-backed-up-data-volume>'
export CERT_SOURCE_CONTROL_VOLUME='<matching-quiesced-control-volume>'
bash scripts/ops/run_recovery_certification.sh accumulated start
```

This copies the source pair through read-only source mounts into uniquely named
targets and starts only the copied targets. Passing this preflight does not by
itself prove the live orchestrator retained its mount. The exact production
boundary still requires a separately authorized maintenance-window recreation
of `web` and `maintenance`, followed by proof that both retained the original
data and control volume identities.

Never use `docker compose down -v`. A code rollback uses the recorded previous
immutable image. A data rollback uses the matching data **and control** backup;
rolling back only one volume can make lifecycle and runtime evidence disagree.

## Evidence and cleanup

After the Workbench journey and operator checks pass:

```bash
export CERT_OPERATOR_ACCEPTED=1
export CERT_GENERATION_ID='<exact-activated-generation>'
export CERT_MANIFEST_DIGEST='<64-character-manifest-digest>'
export CERT_ACTIVATION_INTENT_ID='<signed-activation-intent-uuid>'
bash scripts/ops/run_recovery_certification.sh fresh evidence
```

Use the same mode, run ID, port, image, Vault network, and ephemeral activation
variables as `start`. The
evidence is bounded to resource identities, service health, endpoint results,
SQLite integrity/foreign-key status, and artifact inventory. Add the signed
activation result, generation/manifest identities, bilingual search result,
and operator decision to the approved incident or release record.

Failed targets are retained automatically. After successful evidence review:

```bash
bash scripts/ops/run_recovery_certification.sh fresh cleanup
```

Cleanup refuses unowned or mounted resources. Preserve any target referenced by
an incident, failed restore, activation journal, or unresolved discrepancy.

## Retired startup backups

Older deployments created a full
`BACKUP_DIR/db_backup_YYYY-MM-DD_HHMMSS.sqlite3` copy on every web and
maintenance startup. These flat files are not Vault generations and are not
covered by recovery-set retention. Current entrypoints no longer create them.

Inventory and plan one bounded cleanup batch:

```bash
python manage.py legacy_backup_cleanup plan
```

The plan is read-only. It validates and retains the three newest SQLite copies,
reports deferred debt, and limits one apply operation to 20 GiB. Only after the
Vault generation and isolated restore have passed acceptance may an operator
apply the exact unchanged plan:

```bash
python manage.py legacy_backup_cleanup apply --confirm '<plan-id>'
```

Applying a stale plan, encountering a symlink, changing a candidate, or failing
SQLite integrity stops the operation. Cleanup is never invoked by startup or a
scheduler.

## Stop conditions

Stop when any source is still mounted, a target name matches an active volume,
the image is mutable, data/control backups are not paired, the Vault pointer
changes unexpectedly, integrity or foreign-key checks fail, migrations remain,
an index or bilingual search fails, external side effects are enabled, ingress
is not localhost-only, or only a root-page HTTP response is available.
