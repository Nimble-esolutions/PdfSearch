# Vault Retention and GC Runbook

## Safety posture

Generation retirement is reversible metadata. It does not delete a manifest or
object. Object deletion is not implemented in this release, and
`VAULT_GC_ENABLED=0` is the required deployment posture. Both the server and the
workbench reject GC execution with `gc_execution_disabled`.

Never describe retirement as purge or deletion. Never remove the migrated
generation `legacy-20260725T204411Z-v2c4d9e1` or objects referenced by it.

## Retiring and restoring a generation

Use the Retention & GC section of the Vault Operations Workbench. Retirement
requires a one-use typed confirmation bound to the operator, generation, and
observed state.

Retirement fails closed when the generation is authoritative, runtime-active or
previous, or referenced by a live job. Workspaces and retention holds continue
to protect a retired generation from GC planning. Resolve the reported reason
code and refresh the workbench before trying again. Unretire uses the same
typed-confirmation protections and returns the generation to candidate state.

## Retention holds

Create a hold with a stable reason code and an incident or case reference.
Optional expiry is evaluated server-side. Releasing a hold is idempotent and
append-only audited. A released or expired hold remains in history but no
longer protects a generation.

## GC dry-run planning

A plan can be created only from a fresh, verified inventory and a current
authoritative pointer projection. The planner:

1. selects retired generations older than `VAULT_GC_GRACE_DAYS`;
2. excludes every generation protected by authority, runtime, jobs,
   workspaces, or holds;
3. builds the full manifest reference graph;
4. separates exclusive objects from objects shared with retained generations;
5. binds candidates, inventory version, pointer version, and estimates into a
   canonical plan digest.

Object keys remain server-side and are not returned by the workbench,
diagnostics API, or metrics endpoint. Plans expire and must be recreated from a
fresh inventory. The initial rollout has no deletion implementation, even if a
configuration error sets `VAULT_GC_ENABLED=1`.

## Diagnostics and alerts

The redacted diagnostic export is:

`GET /dashboard/operations/api/v1/diagnostics/`

It includes authority, sync, lease, feature-flag, count, and retention
summaries. It excludes credential aliases, lease tokens, object keys, and raw
exceptions.

Monitor:

- `pdfsearch_vault_retired_generation_count`
- `pdfsearch_vault_active_retention_hold_count`
- `pdfsearch_vault_gc_ready_plan_count`
- `pdfsearch_vault_gc_planned_exclusive_bytes`
- `pdfsearch_vault_gc_execution_enabled`

`PdfSearchVaultGcUnexpectedlyEnabled` is critical. Restore
`VAULT_GC_ENABLED=0`, preserve all plans and objects, record an incident, and
confirm the metric returns to zero. `PdfSearchVaultGcPlanBacklog` is a review
queue warning only; it does not authorize deletion.

## Reason-code guide

| Reason code | Meaning | Operator action |
|---|---|---|
| `generation_authoritative` | Remote projection still points to the generation | Promote a verified replacement through the separate authority flow |
| `generation_runtime_referenced` | Runtime still uses or may roll back to it | Complete activation and runtime retention first |
| `generation_job_in_progress` | A live job references it | Wait, cancel safely, or recover the job |
| `workspace_referenced` | A nonexpired workspace references it | Retain or expire the workspace through its lifecycle |
| `retention_hold_active` | An active hold protects it | Resolve the incident and explicitly release the hold |
| `gc_inventory_stale` | Inventory freshness cannot prove the reference graph | Refresh verified inventory |
| `gc_no_eligible_candidates` | Nothing safely meets policy | Take no action |
| `gc_execution_disabled` | Required initial-rollout safety block | Leave deletion disabled |

## Incident evidence

Record the correlation ID, state version, generation ID, plan digest, inventory
version, pointer version, and safe reason code. Do not copy credentials, lease
tokens, raw S3 responses, object keys, or exception strings into tickets.
