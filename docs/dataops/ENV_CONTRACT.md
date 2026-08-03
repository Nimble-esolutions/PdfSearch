# Data Operations environment contract

The Data Operations control plane uses `DATAOPS_*` variables only. This
contract is intentionally small enough to be copied into Dokploy as a single
environment update. Values are never printed by the application or exported
from the UI; the UI exports key names and redacted placeholders only.

## Resolution order

For every setting the effective value follows the **ENV → DB → default**
precedence order:

1. Process environment (`DATAOPS_*`) — authoritative and read-only in the UI.
2. An encrypted database fallback — used only when the corresponding
   environment key is absent and `DATAOPS_UI_CONFIG_ENABLED=1`.
3. The documented safe default.

The UI may edit fallback values and can produce a reviewed, commented ENV patch
for Dokploy. It must never mutate the running process environment. A restart or
redeploy is required for an ENV change to take effect.

## Profile model

`DATAOPS_PROFILE_MANIFEST` is the canonical JSON list of named profiles. Each
profile has a role (`backup`, `restore`, or `both`), an independent
bucket/dataset, and an explicit namespace/prefix:

```dotenv
DATAOPS_PROFILE_MANIFEST=[{"name":"primary_backup","role":"backup","endpoint":"https://s3.example.invalid","bucket":"example-backups","region":"ap-south-1","dataset_id":"flowdocs-prod","source_id":"prod","namespace":"primary","credential_ref":"DATAOPS_PRIMARY_BACKUP","enabled":true}]
```

The legacy `DATAOPS_ENV_PROFILES` CSV plus `DATAOPS_PROFILE_<NAME>_*` form is
accepted during migration. `DATAOPS_BACKUP_PROFILE` selects a backup
destination and `DATAOPS_RESTORE_PROFILE` selects a restore source;
`DATAOPS_*_SOURCE_PROFILE` and `DATAOPS_*_DESTINATION_PROFILE` provide
per-operation overrides.

The 2026 recovery lineage uses two separate datasets and buckets:

```dotenv
DATAOPS_BACKUP_PROFILE=stage_2026
DATAOPS_RESTORE_PROFILE=stage_2026
# production_v2_source remains an explicit source for clone/rebind only.
```

`clone/rebind` is not an ordinary copy. It is an advanced, typed operation
that requires an explicit source generation, destination profile, and exact
confirmation phrase. It verifies every content-addressed object, rewrites
dataset-bound keys and references, preserves parent manifest lineage, and
advances only the destination authoritative pointer. Dataset mismatch checks
remain enforced for normal backup, restore, copy, and transfer operations.

Keep `DATAOPS_CLONE_REBIND_ENABLED=0` until RustFS bucket registration,
conditional writes, and scoped profile permissions have been proven. The
reference credential is a secret-provider alias; RustFS root credentials are
for one-time bucket provisioning only and must not be passed to the app.

Credential values are supplied by the referenced prefix (`*_ACCESS_KEY` and
`*_SECRET_KEY`) or by an explicitly enabled encrypted database credential. They
are never part of a backup package.

## Runtime and recovery controls

The implementation recognises these controls (all have safe defaults):

| Key | Meaning |
| --- | --- |
| `DATAOPS_ENABLED` | Enable the control plane. |
| `DATAOPS_BACKUP_PROFILE` / `DATAOPS_RESTORE_PROFILE` | Profile IDs selected for each direction. |
| `DATAOPS_BACKUP_MODE` | `manual`, `scheduled`, or `changes`. |
| `DATAOPS_BACKUP_INTERVAL_SECONDS` | Minimum interval for scheduled/change backups. |
| `DATAOPS_MAX_LAG_SECONDS` | Maximum tolerated source lag before a refresh is suggested. |
| `DATAOPS_AUTO_HEAL_ENABLED` | Enable bounded, non-destructive repair. |
| `DATAOPS_AUTO_HEAL_INTERVAL_SECONDS` | Reconciler interval. |
| `DATAOPS_AUTO_HEAL_MAX_RETRIES` | Retry cap per operation. |
| `DATAOPS_OPERATION_LEASE_SECONDS` | Expiring control-plane lease used to prevent duplicate worker execution and reclaim crashed preflight operations. |
| `DATAOPS_AUTO_HEAL_REINDEX_PER_RUN` / `DATAOPS_AUTO_HEAL_REINDEX_PER_DAY` | Reindex budgets. |
| `DATAOPS_STALE_AFTER_SECONDS` | Age after which an observation is stale. |
| `DATAOPS_UI_CONFIG_ENABLED` | Allow editing encrypted DB fallbacks. |
| `DATAOPS_UI_SECRET_STORAGE_ENABLED` | Permit explicitly opted-in encrypted DB credentials. |
| `DATAOPS_CONFIG_ENCRYPTION_KEY` | Key reference for AES-256-GCM fallback values; never log the value. |
| `DATAOPS_RESTORE_AUTO_ACTIVATE_STAGING` | Auto-activate only after all staging gates pass. |
| `DATAOPS_CLONE_REBIND_ENABLED` | Enable the reviewed Advanced-only cross-dataset clone/rebind control. |
| `STAGE_PUBLIC_AUTH_EXCEPTION_REQUIRED` | Declare that restored production authentication data is present on stage. |
| `STAGE_PUBLIC_AUTH_EXCEPTION_APPROVED` | Security-owner approval switch; defaults to blocked. |
| `STAGE_PUBLIC_AUTH_EXCEPTION_OWNER` / `..._MONITORING` / `..._INCIDENT_RESPONSE` / `..._ROLLBACK_AUTHORITY` | Non-secret exception record required before public stage authentication can be enabled. |

Restore quarantine is not an environment setting. The application derives it
as `DATA_ROOT/restore-quarantine`, guaranteeing that web and maintenance use
the same mounted data volume. The `stage_dataops_restore` command still accepts
an explicit `--destination` for disposable operator rehearsals.

## Compatibility window

`ARTIFACT_VAULT_*` names remain accepted as a temporary compatibility profile
when no structured manifest is present. They are never copied into a profile
receipt or exposed as secret values. Generic retired `VAULT_*` credential
aliases continue to fail closed; remove them after all services consume the
manifest.
