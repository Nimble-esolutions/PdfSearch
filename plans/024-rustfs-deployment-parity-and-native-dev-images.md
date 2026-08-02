# Plan 024: Establish RustFS-backed deployment parity and native development images

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report; do not improvise. When done, update this plan's status row in
> `plans/README.md`.
>
> **Drift check (run first)**:
>
> ```bash
> git diff --stat 54e48bc..HEAD -- \
>   docker-compose.yml docker-compose.dev.yml docker-compose.integration.yml \
>   docker-compose.maintenance-e2e.yml .env.example .env.dataops.example \
>   docs/environments README.md DEPLOYMENT.md docs/ENVIRONMENT_CONTRACT.md \
>   docs/ARCHITECTURE_OVERVIEW.md scripts/ci .github/workflows/docker-build.yml
> ```
>
> If any in-scope file changed since this plan was written, compare the
> "Current state" facts against the live files. Treat a material mismatch as a
> STOP condition.

## Status

- **Priority**: P1
- **Effort**: L
- **Risk**: HIGH
- **Depends on**: Plan 006 recurring verification gate
- **Category**: tech-debt / tests / DX / deployment
- **Planned at**: commit `54e48bc`, 2026-08-02
- **Implementation status**: RECONCILE — implementation is complete on
  `feat/rustfs-deployment-parity` at `71a6f11`; Linux AMD64 hosted-runner and
  GitHub PR checks remain to be proven after operator-authorized publication.

## Why this matters

Development currently runs a different object-store implementation and a
weaker deployment contract than stage/production. That is acceptable for a
secondary S3-compatibility test, but not for the canonical development path or
for claims of deployment parity. At the same time, stage/production correctly
consume an immutable application image while development must build the same
application artifact locally.

After this plan, all environments will share one application-plane contract,
RustFS will be the canonical object-store provider, development will build one
native local image used by every application process, and CI will enforce the
intentional differences rather than treating them as accidental drift.

## Architectural decision

Parity is defined at two layers:

| Layer | Development | Stage / production |
|---|---|---|
| Application plane | `web`, `maintenance`, Redis, `/app/data`, `/app/data-control`, health checks, dependency ordering, full critical ENV contract | Same |
| Application artifact | One locally built image shared by `web`, `maintenance`, and the one-shot bucket initializer | One immutable digest shared by `web` and `maintenance` |
| Object-store capability | Isolated single-node RustFS with local-only named volumes and credentials | Existing externally operated RustFS |
| Bucket initialization | Idempotent one-shot initializer against local RustFS | Operator/platform provisioning; application Compose must not create production buckets |
| DataOps profiles | Local RustFS profiles with isolated namespaces and manual-safe defaults | Environment-authoritative profiles and protected credentials |
| MinIO | Secondary S3-compatibility matrix only | Not part of the canonical deployment contract |

Do not require exact service-name equality across every Compose file. RustFS is
an external platform service in stage/production and an in-stack dependency in
development. Require exact equality for the application plane and capability
equivalence for the object-store plane.

Do not use cross-file YAML anchors, Compose `extends`, or implicit `env_file`
inheritance as the contract source. Anchors do not cross files, `extends` would
couple development parsing to production's required immutable digest, and an
implicit environment pass-through can leak host values. Keep explicit Compose
wiring and enforce it with a value-redacted validator.

## Non-negotiable invariants

1. `web` and `maintenance` run the exact same image identity within one
   deployment.
2. Stage/production images are canonical `repository@sha256:<digest>` values,
   have no Compose `build` section, and use `pull_policy: always`.
3. Development has an explicit image name plus identical `build` definitions
   for application services. `docker compose ... up --build` works without
   setting `PDFSEARCH_IMAGE`.
4. A local build never claims to be an immutable production digest.
   `APP_IMAGE_DIGEST` stays empty locally; `APP_RELEASE_VERSION` and the OCI
   revision label carry local source identity.
5. Development never uses stage/production endpoints, buckets, credentials,
   volumes, dataset IDs, source IDs, or namespaces by default.
6. RustFS is the required provider for canonical local and CI lifecycle proof.
   MinIO may remain only as a clearly labelled compatibility target.
7. Both application services receive every key in the critical environment
   contract. Values may differ by environment; key shape may not.
8. `/app/data` and `/app/data-control` remain separate shared mutable mounts.
   Application code remains immutable inside the image.
9. Local backup and restore profiles may share a RustFS bucket only when their
   dataset/namespace combination is collision-free.
10. Local automatic replacement, production activation, external side effects,
    and scheduled publication remain disabled unless a test explicitly enables
    them in a disposable project.
11. No rollout step deletes or reuses the existing `minio_data_dev` volume.
    RustFS receives a new volume name, making rollback recoverable.
12. Validators and logs report key names and reason codes only; they never print
    credential values or complete rendered environments.

## Current state

- `docker-compose.yml:30` gives `web` an immutable `PDFSEARCH_IMAGE` with
  `pull_policy: always`; `maintenance` uses the same image contract at
  `docker-compose.yml:277`.
- Production Compose resolves 204 web environment entries and 155 maintenance
  entries. `scripts/ci/assert_compose_env_parity.py` currently enforces 93
  lifecycle-critical keys across the two services.
- `docker-compose.dev.yml:18` and `:35` define `minio` and `minio-init`.
  `git blame` attributes the default local MinIO server to commit `796226d`
  (`chore(dev): provide local vault and maintenance defaults`).
- `docker-compose.dev.yml:54` and `:169` build `web` and `maintenance`
  separately and do not assign a common image name. A build therefore creates
  service-specific local tags and defaults the OCI revision to `unknown`.
- Development Compose resolves only 90 environment entries for each
  application service and does not provide the new DataOps manifest/selectors.
  The resulting readiness evidence reports `profile_selector_missing`.
- `.github/workflows/docker-build.yml:105-117` validates production Compose and
  production web/maintenance parity, but has no corresponding development
  deployment-contract gate.
- `scripts/ci/run_dev_maintenance_smoke.sh` already creates a unique disposable
  Compose project, builds the development stack, waits for health, runs a
  maintenance smoke test, and removes only that disposable project's volumes.
- The active documentation says that MinIO proves S3-compatible mechanism, not
  deployed RustFS behavior. It does not justify MinIO as the canonical
  development provider.
- Official RustFS documentation supports single-node Docker development,
  ports 9000/9001, ARM64/AMD64 images, and bucket creation through standard S3
  clients. The official image runs as UID 10001; volume permissions must be
  proven on Docker Desktop and Linux rather than assumed.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Compose validator unit tests | `python3 -m unittest scripts.ci.test_compose_env_parity` | all pass |
| Production config | `SECRET_KEY=ci-only OPENAI_API_KEY=ci-only PDFSEARCH_IMAGE=ghcr.io/example/pdfsearch@sha256:$(printf '0%.0s' {1..64}) docker compose -f docker-compose.yml config --quiet` | exit 0 |
| Development config | `docker compose -f docker-compose.dev.yml config --quiet` | exit 0 without `PDFSEARCH_IMAGE` |
| Production contract | same production ENV prefix plus `python3 scripts/ci/assert_compose_env_parity.py --compose-file docker-compose.yml --deployment-mode production` | exit 0; immutable policy verified |
| Development contract | `python3 scripts/ci/assert_compose_env_parity.py --compose-file docker-compose.dev.yml --deployment-mode development` | exit 0; local-build policy verified |
| DataOps tests | `ALLOW_INSECURE_DEFAULTS=1 SECRET_KEY=local-check PYTHONPATH=flowdocs ./.venv/bin/python flowdocs/manage.py test dataops --noinput` | all pass |
| Django checks | `ALLOW_INSECURE_DEFAULTS=1 SECRET_KEY=local-check PYTHONPATH=flowdocs ./.venv/bin/python flowdocs/manage.py check` | no issues |
| Migration drift | `ALLOW_INSECURE_DEFAULTS=1 SECRET_KEY=local-check PYTHONPATH=flowdocs ./.venv/bin/python flowdocs/manage.py makemigrations --check --dry-run` | no changes detected |
| Disposable dev smoke | `bash scripts/ci/run_dev_maintenance_smoke.sh` | local RustFS, web, maintenance, and maintenance job pass |
| Whitespace | `git diff --check` | exit 0 |

Do not use a brace-generated digest in documentation intended for operators;
the expression above is only a local verification convenience.

## Scope

**In scope**:

- `docker-compose.dev.yml`
- `docker-compose.yml` only for application-plane dependency/health parity
  proven necessary by the tests; do not add an in-stack production RustFS
- `docker-compose.integration.yml`
- `docker-compose.maintenance-e2e.yml`
- a new RustFS integration Compose file or overlay under the repository root if
  retaining MinIO compatibility requires separate files
- `scripts/ci/assert_compose_env_parity.py`
- `scripts/ci/test_compose_env_parity.py`
- `scripts/ci/run_dev_maintenance_smoke.sh`
- a new bounded, idempotent local RustFS bucket-initialization script under
  `scripts/runtime/` or `scripts/ci/`, plus its tests
- `.github/workflows/docker-build.yml`
- `.env.example` and `.env.dataops.example`
- `docs/environments/development.env.example`
- `docs/environments/stage.env.example`
- `docs/environments/production.env.example`
- `README.md`, `DEPLOYMENT.md`, `docs/ENVIRONMENT_CONTRACT.md`, and
  `docs/ARCHITECTURE_OVERVIEW.md`
- focused DataOps/Compose tests required to prove the contract
- `plans/README.md` status only

**Out of scope**:

- Installing, upgrading, reconfiguring, or mutating stage/production RustFS
- Accessing a real stage/production bucket during PR validation
- Migrating existing development objects from MinIO into RustFS
- Deleting `minio_data_dev` or any other persistent volume
- Changing DataOps manifest, checksum, activation, or generation semantics
- Making RustFS the Django runtime media backend
- Enabling scheduled backup, automatic restore activation, or external side
  effects by default
- Introducing Kubernetes, Swarm, or a new queue system
- Treating a successful local RustFS test as production recovery certification

## Git workflow

- Create a new feature branch from the current PR branch or current `dev`, as
  directed by the operator. Never commit to `dev` or `main`.
- Use logical conventional commits, for example:
  - `test(compose): define deployment parity policies`
  - `feat(dev): make RustFS and local image builds canonical`
  - `ci(storage): certify RustFS lifecycle and MinIO compatibility`
  - `docs(deploy): document environment-specific image policy`
- Run focused verification before each commit and the complete gate before
  pushing. Open a PR into `dev`; do not merge without operator authorization.

## Steps

### Step 1: Characterize the contract before changing Compose

Extend `scripts/ci/test_compose_env_parity.py` first. Model three explicit
deployment modes: `development`, `staging`, and `production`. Stage and
production share the immutable-image policy; development uses the local-build
policy.

Add test fixtures and reason-code assertions for:

- missing `web`, `maintenance`, or `redis`;
- missing health checks;
- missing internal network membership;
- missing `/app/data` or `/app/data-control` mount targets;
- missing `redis: service_healthy` dependency;
- development missing `rustfs` or its successful one-shot initializer;
- development application services with absent, different, or divergent build
  definitions;
- development application services with absent or different image names;
- production/stage with a mutable image, a build section, non-`always` pull
  policy, divergent images, or `APP_IMAGE_DIGEST` not matching the image;
- any missing or divergent critical environment key;
- extra development-only services being allowed while arbitrary unexpected
  application services do not satisfy a required capability.

Keep every error value-redacted. Tests must assert that sentinel credential and
image values never appear in errors or CLI output.

**Verify**:

```bash
python3 -m unittest scripts.ci.test_compose_env_parity
```

Expected: new tests fail only because the current validator lacks the new
deployment-mode behavior. Commit the tests only after the matching validator
lands in Step 2, unless the repository accepts intentionally red commits.

### Step 2: Turn the parity script into a deployment-contract validator

Extend `scripts/ci/assert_compose_env_parity.py` without creating a second
overlapping validator.

Add `--deployment-mode development|staging|production`. Preserve `production`
as the default for backward compatibility. Separate validation into named,
unit-testable functions:

- environment-key parity;
- core-service topology;
- mount/network/health/dependency semantics;
- immutable application-image policy;
- local-build application-image policy;
- development RustFS capability topology.

The CLI must emit bounded reason codes or key names only. It must not dump
rendered Compose JSON, environment values, image references, mount source
paths, or credentials.

**Verify**:

```bash
python3 -m unittest scripts.ci.test_compose_env_parity
```

Expected: all tests pass, including redaction tests.

### Step 3: Define one native development application image

In `docker-compose.dev.yml`, introduce a file-local extension anchor containing:

- `image: ${PDFSEARCH_DEV_IMAGE:-pdfsearch-dev:local}`;
- the current Dockerfile build context;
- build arg `OCI_REVISION: ${LOCAL_BUILD_REVISION:-local-dev}`.

Merge this anchor into `web`, `maintenance`, and the one-shot bucket
initializer if the initializer uses the application image. Do not use
`PDFSEARCH_IMAGE` for development. Keep the canonical command:

```bash
LOCAL_BUILD_REVISION="$(git rev-parse --short HEAD)" \
docker compose -f docker-compose.dev.yml up -d --build --wait
```

Pass `APP_RELEASE_VERSION=${LOCAL_BUILD_REVISION:-local-dev}` to both
application services and leave `APP_IMAGE_DIGEST` empty. Standardize the
maintenance entrypoint path and health-check behavior across development and
production where doing so does not change policy.

After startup, the smoke script must compare container image IDs for `web` and
`maintenance`, inspect the OCI revision label, and compare it with the
non-secret runtime release identity. Fail with reason codes only.

**Verify**:

```bash
docker compose -f docker-compose.dev.yml config --quiet
python3 scripts/ci/assert_compose_env_parity.py \
  --compose-file docker-compose.dev.yml \
  --deployment-mode development
```

Expected: config renders without `PDFSEARCH_IMAGE`; the only initial validator
failure may be the not-yet-migrated RustFS service from Step 4.

### Step 4: Replace canonical local MinIO with isolated RustFS

Replace `minio` and `minio-init` in `docker-compose.dev.yml` with `rustfs` and
`rustfs-init`.

Requirements:

- Select a reviewed official `rustfs/rustfs` release that publishes both
  `linux/amd64` and `linux/arm64`. Pin a release identifier for developer use;
  CI must resolve and record its immutable digest before lifecycle tests.
- Never use `latest` as release evidence.
- Bind any exposed S3/console ports to `127.0.0.1` only.
- Use new named volumes such as `rustfs_data_dev` and, if required by the
  official image, `rustfs_logs_dev`. Do not mount or rename `minio_data_dev`.
- Use local-only defaults for RustFS credentials. Never copy stage/production
  credentials into examples or Compose.
- Respect the official image's non-root UID 10001. Prove named-volume startup
  on Linux AMD64 and Docker Desktop ARM64. Do not add privileged mode or a
  blanket host-directory `chown` workaround.
- Do not assume a health endpoint or utility exists inside the image. Inspect
  the selected image's health configuration. The one-shot initializer must
  retry a signed S3 operation with a bounded deadline and becomes the readiness
  barrier for `web`.
- Implement bucket initialization with the already installed boto3 client in a
  small repository script. It must create exactly the configured local bucket,
  treat the already-owned bucket response as success, reject non-local endpoint
  configuration in development, use bounded retries, and never log credentials.
- `web` depends on successful `rustfs-init`; `maintenance` depends on Redis,
  successful RustFS initialization, and healthy `web` during initial startup.

Using the MinIO `mc` client against RustFS is officially supported, but the
canonical stack should avoid an extra client image when the application image
already has boto3. This also removes accidental dependence on a second object-
store vendor from the default topology.

**Verify**:

```bash
docker compose -f docker-compose.dev.yml config --quiet
bash scripts/ci/run_dev_maintenance_smoke.sh
```

Expected: a fresh disposable project initializes RustFS, both application
services become healthy, and the maintenance smoke passes without MinIO server
containers.

### Step 5: Give development a complete, safe DataOps profile contract

Pass the full `CRITICAL_KEYS` set to both development application services.
Match key presence, not production values.

Set local defaults equivalent to this logical manifest:

```json
[
  {
    "name": "local_restore",
    "role": "restore",
    "endpoint": "http://rustfs:9000",
    "bucket": "pdfsearch-dev",
    "dataset_id": "ai-sahakar-dev",
    "source_id": "local-dev",
    "namespace": "restore",
    "credential_ref": "DATAOPS_LOCAL_RUSTFS",
    "enabled": true
  },
  {
    "name": "local_backup",
    "role": "backup",
    "endpoint": "http://rustfs:9000",
    "bucket": "pdfsearch-dev",
    "dataset_id": "ai-sahakar-dev",
    "source_id": "local-dev",
    "namespace": "backup",
    "credential_ref": "DATAOPS_LOCAL_RUSTFS",
    "enabled": true
  }
]
```

Use `DATAOPS_BACKUP_PROFILE=local_backup` and
`DATAOPS_RESTORE_PROFILE=local_restore`. Pass complete
`DATAOPS_LOCAL_RUSTFS_ACCESS_KEY` and `DATAOPS_LOCAL_RUSTFS_SECRET_KEY` pairs
from local-only variables to both services. The profile pair intentionally
shares one bucket and dataset but has distinct namespaces.

Development defaults:

- `DATAOPS_ENABLED=1`;
- backup mode `manual`;
- auto-heal disabled;
- restore auto-activation disabled;
- UI profile configuration enabled;
- UI secret entry disabled;
- external side effects sandboxed;
- backup role disabled unless a disposable test explicitly changes it.

Add a Compose-resolved configuration test that loads the manifest without
printing it, resolves both profiles, validates both selectors, checks complete
credential references, and proves no namespace collision.

**Verify**:

```bash
ALLOW_INSECURE_DEFAULTS=1 SECRET_KEY=local-check PYTHONPATH=flowdocs \
  ./.venv/bin/python flowdocs/manage.py test dataops --noinput
```

Expected: all DataOps tests pass, including the new development manifest test.

### Step 6: Make RustFS the primary lifecycle gate and MinIO secondary

Do not delete the existing MinIO integration coverage immediately. First add a
RustFS-backed disposable lifecycle using the same test assertions:

- bucket access and permission failures;
- conditional create/update and fencing;
- multipart/object checksum behavior used by the application;
- backup publication and authoritative pointer update;
- restore staging, partial-object rejection, checksum verification, database/
  media reconciliation, reindex, activation, rollback, and process death;
- same-bucket isolated namespaces and collision rejection.

The RustFS lifecycle is required on every relevant PR and release. Rename the
existing MinIO job/output to explicitly say `MinIO S3 compatibility`; it must
not satisfy a RustFS certification or deployment-parity check. If total CI time
becomes unacceptable, keep the bounded MinIO compatibility subset on PRs and
run its full lifecycle on a scheduled workflow. Do not weaken the RustFS gate.

Resolve RustFS and compatibility dependency images to immutable digests in CI.
Never silently fall back from RustFS to MinIO when the RustFS job fails.

**Verify**:

```bash
bash scripts/ci/run_dev_maintenance_smoke.sh
bash scripts/ci/run_vault_integration.sh
```

Expected: the canonical development/RustFS gate passes; the separately named
MinIO compatibility gate also passes or has an explicitly documented reduced
scope.

### Step 7: Enforce every deployment mode in GitHub Actions

Update `.github/workflows/docker-build.yml` to run:

1. production Compose rendering and production deployment contract;
2. development Compose rendering without `PDFSEARCH_IMAGE` and development
   deployment contract;
3. unit tests for the validator;
4. native local-image/RustFS smoke using the already built CI application image
   when possible;
5. required RustFS lifecycle proof;
6. separately labelled MinIO compatibility proof.

Avoid rebuilding the same application image multiple times. Allow the
development smoke script to accept a prebuilt local image and `--no-build` in
CI, while retaining `--build` as the default developer behavior. The smoke
must still verify that `web` and `maintenance` use the same image ID.

**Verify**:

```bash
python3 -m unittest scripts.ci.test_compose_env_parity
python3 scripts/ci/assert_compose_env_parity.py \
  --compose-file docker-compose.dev.yml \
  --deployment-mode development
```

Expected: local checks pass. After pushing, all new GitHub jobs must be green;
skipped compatibility jobs must have an event-based condition documented in
the workflow.

### Step 8: Reconcile examples and operator documentation

Document one deployment matrix consistently in `.env.example`, environment
examples, README, deployment guides, environment contract, and architecture
overview.

Required distinctions:

- identical application services and critical key shape;
- local application image build versus immutable stage/production image pull;
- local in-stack RustFS versus externally operated stage/production RustFS;
- local-only credentials and bucket names;
- MinIO compatibility tests are not RustFS certification;
- `APP_RELEASE_VERSION` is local source identity;
- `APP_IMAGE_DIGEST` is immutable deployed image identity and remains empty for
  native local builds;
- canonical local command includes `--build --wait`;
- no normal command uses `docker compose down -v` against retained data;
- rollback preserves the old MinIO volume and the new RustFS volume.

Do not put secret values into examples. Placeholder credentials must be
visibly local/test-only. Stage/production examples must include the full
DataOps key shape or explicitly source the canonical protected environment
contract; they may not depend on an undocumented second file.

**Verify**:

```bash
python3 -m unittest scripts.ci.test_docs_contract
python3 scripts/ci/docs_contract.py
python3 -m unittest scripts.ci.test_operator_language
python3 scripts/ci/validate_operator_language.py
```

Expected: all documentation and operator-language checks pass.

### Step 9: Run clean-volume, retained-volume, and rollback drills

Use uniquely named disposable Compose projects. Do not touch the operator's
normal local volumes.

Record:

1. Fresh RustFS volume: build, initialize, publish, restore into a separate
   namespace, reconcile, reindex, activate, and verify readiness.
2. Retained volume: restart/recreate application containers with a newly built
   local image; prove database, media, indexes, RustFS objects, active
   generation, and manifest identity persist.
3. Failed restore: inject a partial object set or checksum mismatch; prove the
   old generation remains active.
4. RustFS restart/process death: prove idempotent bucket initialization and
   operation lease recovery.
5. Rollback: run the previous application image against the retained
   application volumes without deleting RustFS data; prove the prior generation
   remains recoverable.
6. Architecture: run the development smoke on Linux AMD64 and Docker Desktop
   ARM64, specifically checking RustFS volume ownership and image availability.

Evidence must contain IDs, digests, counts, statuses, and timestamps only. It
must not contain credentials or object contents.

**Verify**:

```bash
git diff --check
ALLOW_INSECURE_DEFAULTS=1 SECRET_KEY=local-check PYTHONPATH=flowdocs \
  ./.venv/bin/python flowdocs/manage.py check
ALLOW_INSECURE_DEFAULTS=1 SECRET_KEY=local-check PYTHONPATH=flowdocs \
  ./.venv/bin/python flowdocs/manage.py makemigrations --check --dry-run
```

Expected: all commands pass, no migration is generated, and all drill evidence
names the exact application image/revision and data generation used.

## Test plan

### Unit tests

- Deployment-mode parsing and backward-compatible production default.
- Core-service topology and capability-provider validation.
- Immutable versus local-build image policy.
- Build-definition and image-name equality.
- Health, network, dependency, and mount-target requirements.
- Environment-key parity and redacted errors.
- Local RustFS initializer retries, idempotency, endpoint containment, timeout,
  permission failure, and redacted logging.
- Local DataOps manifest parsing, selector roles, credential completeness, and
  namespace isolation.

### Compose contract tests

- Production config with an immutable placeholder digest.
- Development config without `PDFSEARCH_IMAGE`.
- Same application image identity across `web` and `maintenance`.
- RustFS/init required in development but not embedded into production.
- Production extra legacy mount and Dokploy network accepted as intentional.
- Development extra local RustFS services accepted as intentional.

### Integration tests

- RustFS bucket initialization and object-store capability probe.
- Backup/restore publication matrix, partial transfers, permission failures,
  conditional writes, and process death.
- MinIO compatibility subset remains separately named.
- Fresh and retained named-volume behavior.

### End-to-end test

1. Build one local application image with the current revision.
2. Start Redis and isolated RustFS.
3. Initialize the local bucket idempotently.
4. Restore from `local_restore` into a new generation.
5. Verify manifest/checksums, reconcile database/media, and reindex.
6. Activate only after verification and retain the prior generation.
7. Publish to `local_backup` in the distinct namespace.
8. Restore that backup into another isolated namespace.
9. Recreate application containers from the same image and verify persistence.
10. Rebuild a new local image and prove data survives the application rollout.

## Rollout and rollback

Roll out in this order: validator tests, validator implementation, local image
identity, RustFS service, local profiles, RustFS CI gate, compatibility gate,
documentation, then drills.

The first rollout must create new RustFS volumes and leave MinIO volumes
untouched. No automatic object migration is part of this plan. If rollback is
required, revert the Compose/code commits and restart the prior development
stack; the old MinIO volume remains available. Preserve the RustFS volume for
forensics or later explicit migration. Report exactly which stack and volume
set is active.

Stage/production rollout is configuration-only after CI passes. Do not change
or restart the existing RustFS service under this plan. Validate the application
image digest, profile configuration posture, endpoint permissions, and
capabilities using non-destructive probes before recreating application
services. Production data operations remain fail-closed until operator-approved
certification exists.

## Done criteria

- [ ] Development Compose renders and starts without `PDFSEARCH_IMAGE`.
- [ ] One locally built image ID is used by `web` and `maintenance`.
- [ ] Local OCI revision and runtime `APP_RELEASE_VERSION` agree.
- [ ] Production/stage still require one immutable digest and contain no build
      definition.
- [ ] Core application services, mounts, networks, health checks, dependencies,
      and critical environment keys pass the deployment-mode validator.
- [ ] Canonical development uses RustFS; no MinIO server is started.
- [ ] Local RustFS works on Linux AMD64 and Docker Desktop ARM64.
- [ ] Development resolves valid backup and restore profiles with isolated
      namespaces and complete local-only credentials.
- [ ] `/readyz` no longer reports DataOps `profile_selector_missing` under the
      canonical development configuration. Empty-data readiness may remain
      intentionally non-ready.
- [ ] RustFS lifecycle is a required CI gate.
- [ ] MinIO is labelled and scoped as compatibility testing only.
- [ ] Clean-volume, retained-volume, failed-restore, process-death, and rollback
      drills pass.
- [ ] No existing MinIO or application data volume is deleted or repurposed.
- [ ] All commands in "Commands you will need" pass.
- [ ] `git diff --check` passes and no secrets appear in tracked files or logs.
- [ ] Documentation describes one consistent deployment matrix.
- [ ] GitHub PR checks are green before merge.

## STOP conditions

Stop and report; do not improvise if:

- the selected RustFS image lacks a reviewed version or either required
  architecture;
- RustFS cannot start on named volumes without privileged mode, broad host
  ownership changes, or an undocumented permission workaround;
- required S3 conditional operations used for fencing differ on RustFS;
- the local initializer would need access to a non-local endpoint or bucket;
- development requires real stage/production credentials to pass;
- replacing MinIO requires deleting, converting, or silently abandoning an
  existing user volume;
- production Compose would need to embed or administer the external RustFS
  cluster;
- the application services cannot use one image without changing their runtime
  code or entrypoint contract;
- a validator needs to print values to diagnose failure;
- RustFS lifecycle tests pass only by weakening checksum, namespace, fencing,
  permission, activation, or rollback requirements;
- a test would mutate production/stage data without explicit operator approval;
- any in-scope file materially drifted from this plan's current-state evidence.

## Maintenance notes

- Adding a lifecycle-critical setting requires updating the critical key set,
  every application service in every deployment mode, environment examples,
  and validator tests in the same PR.
- Adding an application process requires declaring which application image it
  uses and whether it shares data/control mounts.
- Upgrading RustFS requires image-architecture inspection, capability tests,
  fresh/retained-volume drills, and release-note review before changing the
  pinned development/CI version.
- S3 compatibility and RustFS certification are separate claims. Keep job names
  and documentation explicit.
- A future production RustFS topology change belongs in a separate operator-
  approved infrastructure plan; this plan governs application integration and
  development parity only.

## Primary external references

- RustFS Docker installation: `https://docs.rustfs.com/installation/docker/`
- RustFS bucket creation: `https://docs.rustfs.com/management/bucket/creation`
- RustFS upstream repository: `https://github.com/rustfs/rustfs`
- Compose specification: `https://github.com/compose-spec/compose-spec/blob/master/spec.md`
