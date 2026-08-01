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

`DATAOPS_ENV_PROFILES` is a comma-separated list of profile IDs. Each profile
has a role (`backup`, `restore`, or `both`) and an independent bucket/dataset
target:

```dotenv
DATAOPS_ENV_PROFILES=primary_backup,stage_restore
DATAOPS_PROFILE_PRIMARY_BACKUP_ROLE=backup
DATAOPS_PROFILE_PRIMARY_BACKUP_ENDPOINT=https://s3.example.invalid
DATAOPS_PROFILE_PRIMARY_BACKUP_BUCKET=example-backups
DATAOPS_PROFILE_PRIMARY_BACKUP_REGION=ap-south-1
DATAOPS_PROFILE_PRIMARY_BACKUP_DATASET_ID=flowdocs-prod
DATAOPS_PROFILE_PRIMARY_BACKUP_SOURCE_ID=prod
DATAOPS_PROFILE_PRIMARY_BACKUP_CREDENTIAL_PREFIX=DATAOPS_PRIMARY_BACKUP
```

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
| `DATAOPS_AUTO_HEAL_REINDEX_PER_RUN` / `DATAOPS_AUTO_HEAL_REINDEX_PER_DAY` | Reindex budgets. |
| `DATAOPS_STALE_AFTER_SECONDS` | Age after which an observation is stale. |
| `DATAOPS_UI_CONFIG_ENABLED` | Allow editing encrypted DB fallbacks. |
| `DATAOPS_UI_SECRET_STORAGE_ENABLED` | Permit explicitly opted-in encrypted DB credentials. |
| `DATAOPS_CONFIG_ENCRYPTION_KEY` | Key reference for AES-256-GCM fallback values; never log the value. |
| `DATAOPS_RESTORE_AUTO_ACTIVATE_STAGING` | Auto-activate only after all staging gates pass. |

## Hard cutover

`ARTIFACT_VAULT_*` and `VAULT_*` are retired. Their presence is a startup
configuration error with key names only (never values). There are no aliases;
remove the old keys from Dokploy before deploying the Data Operations build.
