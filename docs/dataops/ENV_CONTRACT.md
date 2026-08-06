Status: Active
Audience: Operator, Developer
Owner: FlowDocs maintainers
Last verified: 2026-08-06
Canonical source: docs/dataops/ENV_CONTRACT.md

# Data Operations v3 environment contract

DataOps v3 deliberately keeps normal operation configuration small. The
operator chooses an outcome—Backup, Restore, Test recovery, or Import—and the
lifecycle planner derives the route from dataset ownership and provenance.
Profile roles, source/destination selectors, clone flags, and same-dataset
exceptions are not part of the supported operator contract.

## Standard settings

| Key | Purpose | Safe posture |
| --- | --- | --- |
| `DATAOPS_ENABLED` | Makes the Data protection workbench and v3 executor available | `1` only after the owned connection can be checked |
| `APP_ENV` | Selects development, staging, production, review, or test policy strength | Exact deployment environment |
| `DEPLOYMENT_ID` | Stable identity bound into plans and receipts | Unique and persistent |
| `DATASET_ID` | Dataset owned by this instance | Stable; must match the owned connection |

The runtime configuration digest contains only redacted connection metadata,
environment identity, and policy. It never contains credentials.

## One owned recovery connection

The control database may hold one primary `DataConnection` for the local
dataset. DataOps uses it for backup and same-dataset restore. Import may read a
separate foreign connection, but that source never becomes the local owner and
is never selected through environment profile choreography.

Resolution is deterministic:

1. use the stored primary connection whose dataset matches `DATASET_ID`;
2. otherwise compile a temporary bootstrap connection from
   `ARTIFACT_VAULT_ENDPOINT`, `ARTIFACT_VAULT_BUCKET`, region, and the
   deployment-injected credential pair;
3. if neither exists, show a typed `owned_connection_*` requirement and do not
   queue a storage operation.

The temporary bootstrap accepts:

```dotenv
ARTIFACT_VAULT_ENABLED=1
ARTIFACT_VAULT_ENDPOINT=https://rustfs.example.invalid
ARTIFACT_VAULT_BUCKET=<owned-recovery-bucket>
ARTIFACT_VAULT_REGION=us-east-1
```

`ARTIFACT_VAULT_ACCESS_KEY`, `ARTIFACT_VAULT_SECRET_KEY`, and an optional
session token stay in the deployment secret provider. When the pair is present,
the bootstrap records the internal `env://ARTIFACT_VAULT` reference and only
the worker resolves it at execution time. Stored connections also support an
approved file-backed server-side credential reference. The browser, plan,
receipt, audit event, and diagnostics never receive secret values.

Before publication, the connection check proves bucket access, dataset
ownership, read/write capability, conditional writes, and the required object
store semantics. A new empty bucket may be healthy after this check; it does
not contain a recovery point until a backup or import succeeds.

## Policy and routing

Policy lives in the control database. If no row exists, DataOps derives safe
defaults from `APP_ENV`; ordinary retry, interval, concurrency, activation, and
route decisions are not assembled from dozens of environment flags.

| Intent | DataOps decision |
| --- | --- |
| Backup | Snapshot the active authoritative data and publish one complete v3 recovery point to the owned connection |
| Restore | Require a same-dataset recovery point and prepare a new isolated candidate |
| Import | Read a foreign/legacy source, copy and verify missing objects, rebind ownership, preserve parent lineage, then prepare a candidate |
| Test recovery | Restore into a disposable target and prove integrity/readiness without changing the active pointer |

Activation remains separately signed and confirmation-gated. A successful
backup, import, or restore never silently changes the runtime pointer.

## Compatibility settings

The application still parses old `DATAOPS_PROFILE_*`, `VAULT_*`, and some
special-case feature flags because selected maintenance endpoints, integration
fixtures, and the signed activation bridge have not yet been migrated. Their
presence in settings or Compose is not a supported operator contract.

| Family | Current status | Standard action |
| --- | --- | --- |
| `DATAOPS_PROFILE_MANIFEST`, `DATAOPS_ENV_PROFILES`, `DATAOPS_*_PROFILE` selectors | DataOps v2 compatibility | Leave blank in a new v3 deployment |
| `DATAOPS_CLONE_REBIND_ENABLED`, same-dataset/auto-activate switches | Retired operator choices | Leave disabled; v3 derives the route and keeps activation separate |
| `VAULT_SYNC_*` and mutation/snapshot tuning | Disabled legacy publication path | Keep `VAULT_SYNC_ENABLED=0` |
| `VAULT_RESTORE_*`, profile UI, and admin mutation flags | Disabled legacy restore/API path | Keep restore/admin/profile UI flags disabled |
| `vaultops` activation/runtime settings | Temporary internal bridge | Change only through the signed activation runbook and impact review |

Do not remove these keys from code or Compose merely because they are absent
from the standard contract. First trace all callers, migrate durable control
records, preserve rollback and crash recovery, update web/maintenance parity,
and certify recovery in a separate code-change PR.

See [V3_ARCHITECTURE.md](V3_ARCHITECTURE.md) for lifecycle invariants and
[V3_IMPACT_ANALYSIS.md](V3_IMPACT_ANALYSIS.md) for the cleanup backlog.
