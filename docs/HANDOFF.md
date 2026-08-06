Status: Active, living handoff
Audience: Maintainer, Operator, Developer, AI agent, Reviewer
Owner: FlowDocs maintainers
Last verified: 2026-08-06
Canonical source: docs/HANDOFF.md
Supersedes: docs/STATUS-2026-08-03.md for current operational state
Update trigger: Every merged runtime/release/data/operations change, deployment, incident, rollback, or material blocker decision

# Current project handoff

## Read this first

This is the single secret-free source for current repository, stage, legacy
production, data-recovery, and next-action state. Read it before changing code,
deployment configuration, persistent data, recovery behavior, or environment
posture.

The dated `STATUS-*.md` files are immutable evidence snapshots. They explain
how the current state was reached, but they do not override this handoff.
Detailed contracts remain in their owning runbooks; this page links to them
instead of duplicating their procedures.

Evidence on this page was refreshed on 2026-08-06 (Asia/Kolkata). A live system
can change after that time, so repeat the read-only checks in
[Resume checks](#resume-checks) before a mutation.

## Non-negotiable boundary

- Legacy production at `www.ai-sahakar.net` remains authoritative and online.
- Production traffic, DNS, Traefik routing, the legacy service, and
  `prod_flowdocs` were not changed by the 2026 stage rehearsal.
- The project named `sahakar-ai-sahakar-frontend-2026-prod-ruhj6z` is **stage**
  despite the historical `prod` substring. It serves `2026.ai-sahakar.net`.
- `/root/prod-2026.env` is a prepared future-production configuration file. No
  corresponding 2026 production Dokploy project exists yet, and the file alone
  does not authorize or constitute a deployment.
- Production cutover requires a separate explicit approval, immutable image
  selection, rollback evidence, and traffic-change plan.
- Never use `docker compose down -v`, delete or reuse a source volume as a
  restore target, or make the legacy mount writable.

## Current verified state

| Boundary | Verified state | Evidence / consequence |
| --- | --- | --- |
| Repository integration baseline | `dev` contains `690ed888b30c0b61ce2ac3bc5824457469b83cf0` | PR #185 is merged; Classic and Workbench are both part of the integration baseline |
| Local development | Development Compose stack is currently stopped | Do not infer local data fitness from historical round-trip evidence; start and verify it when local runtime work resumes |
| Stage route | `https://2026.ai-sahakar.net/` returned HTTP 200 | Reachability only; `/readyz` remains authoritative |
| Stage services | Redis, web, and maintenance are running and healthy | Same Compose project and persistent volumes remain active |
| Stage application artifact | `ghcr.io/nimble-esolutions/pdfsearch/shakar-frontend@sha256:b38d887f784a141fe5c3d2d2ca68e721b96d92b76a50e71129d7dcbac120323c` | Running OCI revision is `690ed888b30c0b61ce2ac3bc5824457469b83cf0` (PR #185) |
| Repository versus stage | Stage and `dev` both run revision `690ed888b30c0b61ce2ac3bc5824457469b83cf0` | The search-intent correction described below is locally verified but not yet merged or deployed |
| Stage readiness | `status=ready`; database, cache, migrations, data, backup, and DataOps checks are `ok` | Backup, restore, and import actions report `ready` |
| Stage inventory | 242 PDF rows, 242 indexed PDFs, 46 folders, 7 users | Indexing ratio is `1.0` |
| Signed runtime | Generation `dataops-import-legacy-20260802-86288855-stage-2026` | Signature verifies; runtime pointer is authoritative |
| Active manifest | `80d8dc81d27956d86054953d1651ff5a88e1597449fc73c34f82a351c1a83c96` | Exact digest reported by `/readyz` |
| Stage recovery receipts | Backup and restore receipts are present | Stage backup remains manual and is not a web-availability prerequisite |
| Legacy production | `www.ai-sahakar.net` returned HTTP 200; Swarm service `sahakar-dev-frontend-dockerfile-1cubi5` remains at one replica | Legacy production remains unchanged and authoritative |
| Environment files | `/root/stage-2026.env` and `/root/prod-2026.env` are present | Never print or commit their values; audit posture by key only |

## Data and recovery lineage

```text
legacy production (authoritative, read-only source)
  prod_flowdocs
    -> stable source generation legacy-20260802T085639Z-86288855
       dataset ai-sahakar-prod-v2
       bucket ai-sahakar-prod-flowdocs-artifact-vault-v2
         -> verified clone/rebind clone-legacy-20260802T085639Z-86288855
            dataset ai-sahakar-stage-2026
            bucket ai-sahakar-stage-2026-flowdocs-artifact-vault
              -> reconciled stage import
                 -> signed active generation
                    dataops-import-legacy-20260802-86288855-stage-2026
                    manifest 80d8dc81...3c96
                      -> manual stage backup
                         -> isolated disposable restore rehearsal
```

The stage clone contains 416 logical objects and 1,093,501,777 bytes. A
recovery point is logically full: “incremental” means unchanged
content-addressed objects can be reused instead of uploaded again. It is not a
fragile chain of partial archives.

The latest readiness evidence reports the stage backup receipt manifest as
`01aa7e0ff7c8ac6583c35098f6b3aab55eb480eaa99f0ea32a972e868f3b0e31`
and the restore receipt manifest as the active manifest above. Automatic stage
backup remains off unless the operator explicitly changes that policy.

## Runtime and policy posture

| Concern | Current decision |
| --- | --- |
| Data Operations | DataOps v3 is the active backup/import/restore contract; do not restore the superseded VaultOps UI or parallel control path |
| Stage image selector | `:latest` is intentional for stage; the resolved running digest and OCI revision are the evidence |
| Production image selector | Must be an explicitly approved immutable `repo@sha256:<digest>` |
| Stage public authentication | Production-account exposure exception is approved and its owner, monitoring, incident response, and rollback authority are recorded |
| Search provider | Real external AI is enabled for the AI lane; unrelated external side effects remain sandboxed |
| Document custody | Original PDFs stay on the server; native extraction runs first and local OCR handles scanned/blank pages |
| OCR | Local Tesseract fallback supports English, Marathi, and Hindi (`eng+mar+hin`) with bounded pages, pixels, and time |
| Embeddings | Extracted text may reach the configured external embedding provider under the existing side-effect policy |
| Backup cadence | Stage backup is manual; a successful receipt is recovery evidence, not a readiness prerequisite |
| Activation | Signed runtime pointer plus exact manifest digest is required; quarantine presence and HTTP 200 are insufficient |
| Public search presentation | Classic search is the fail-closed primary view; Knowledge Workbench remains isolated and can be selected by a superadmin or previewed with `?view=workbench` |
| Public search response contract | Exact standalone greetings/thanks/identity prompts are `small_talk`; document questions are `evidence_answer` or `no_evidence`; both themes consume the same typed JSON contract |

The last controlled evidence search confirmed that `Society election rules`
returned a real answer with three protected references. The exact stage query
`give me most updated rules about societies` instead returned the generic
greeting because the legacy small-talk predicate used substring matching:
`updated` contains `date`, while ordinary words such as `this`, `membership`,
and `historical` contain `hi`. This predates the theme engine and is a shared
backend defect, not a Classic- or Workbench-specific failure.

The current correction uses normalized whole-query intent matching, adds typed
response outcomes, and retains the existing `answer` and `references` fields
for compatibility. Its packaged-image verification covers 44 Django tests and
60 Playwright checks across both themes and four viewports. Do not call it
deployed until its merged OCI revision and stage canaries are recorded here.

## Recently completed work

| PR | Outcome |
| --- | --- |
| #176 | Simplified backup, import, restore, and recovery around DataOps v3 |
| #177 | Corrected public search routing across authorized documents |
| #178 | Unified operator-workbench truth, scale behavior, and stage image policy |
| #179 | Added pre-merge maintenance lifecycle certification |
| #180 | Added reviewed multi-file intake for up to 50 PDFs with queued processing |
| #181 | Safely applies recovery-backed additive migrations to an activated runtime |
| #182 | Recorded and closed the activated-runtime migration incident |
| #183 | Corrected Compose volume-ownership recovery guidance |
| #184 | Established this enforced living project handoff |
| #185 | Rebuilt the approved Classic public search as the primary view while preserving an isolated Knowledge Workbench secondary view |

The stage volume warning is resolved. The three project-scoped volumes were
copied while quiescent, digest-verified, recreated with Docker Compose's
internal ownership labels, restored, re-verified, and restarted. No
`COMPOSE_PROJECT_NAME`, `external: true`, custom backup labels, or
Dokploy-specific volume workaround was added. A second Compose apply emitted
no ownership warning.

## Open decisions and next actions

There is no data-readiness blocker. The active search-intent candidate must be
merged and deployed before the reported greeting misclassification is resolved
on stage. Remaining work is decision-driven:

1. **Future production project:** create and validate the dedicated 2026
   production Dokploy project only after explicit approval. Treat
   `/root/prod-2026.env` as prepared input, not deployment evidence.
2. **Production rehearsal:** before traffic changes, select an immutable image,
   validate rendered Compose and key-only environment posture, restore into
   isolated production-candidate volumes, run search/PDF/auth smoke checks, and
   record rollback image and generation.
3. **Production cutover:** remains out of scope until separately authorized.
   Do not change legacy service routing or `prod_flowdocs` while preparing it.
4. **Stage recovery retest:** run another manual backup and disposable restore
   only when recovery/data contracts change or when explicitly requested.
5. **Local development:** start the native development stack and rerun focused
   local recovery/search tests when a new implementation task requires it; the
   stack is intentionally stopped now.
6. **Search-intent release:** publish the verified candidate through the normal
   image pipeline, deploy it to stage, and canary `/` plus
   `/?view=workbench`. Prove exact greetings remain `small_talk`, the reported
   rules query enters evidence search, source links render safely, and the
   signed runtime/readiness evidence remains unchanged. No ENV or database
   migration is required.

## Known traps that must not recur

- A root-page 200 or healthy container does not prove signed data readiness.
- `docker system prune --all --volumes` does not repair ownership labels and
  must not be used as a volume-recovery technique.
- Do not silence Compose ownership warnings by making application volumes
  external, adding a project-name variable, or adding arbitrary YAML labels.
- Do not pre-create ordinary project-scoped volumes without Compose's internal
  labels. If historical labels are missing, use the verified copy/recreate/
  restore procedure in
  [DOKPLOY_DATA_PERSISTENCE.md](DOKPLOY_DATA_PERSISTENCE.md).
- Do not expose enabled UI controls whose real handler will reject the default
  request. Capability, default action, authored refusal, and retry behavior
  must be tested together.
- Never classify conversational intent with substring matching. A small-talk
  fast path must match the complete normalized query, and regression tests must
  include domain words containing short conversational tokens.
- Both public themes must preserve the backend response `kind`; a friendly
  answer without evidence must not be presented as a document-backed answer.
- Do not deploy a migration to an activated SQLite runtime unless the safe
  runtime migration classifier accepts it as recovery-backed and additive.
- Do not resurrect VaultOps as a parallel product surface. DataOps v3 replaced
  it to reduce operator and code complexity.

## Resume checks

Run these read-only checks before continuing operational work:

```bash
git checkout dev
git pull --ff-only origin dev
git status --short --branch
gh pr list --state open
curl -fsS https://2026.ai-sahakar.net/readyz
curl -fsS -o /dev/null -w '%{http_code}\n' https://www.ai-sahakar.net/
```

For server inspection, use the approved SSH host from the local operations
inventory and summarize environment posture by key; never print `.env` values.
Verify the stage Compose project, running digest/revision, volume labels,
database integrity, counts, signed pointer, receipts, and active jobs before a
mutation.

## Handoff update contract

“Keep it updated” means event-driven, evidence-backed updates in the same PR or
immediately after an approved remote operation—not speculative timestamps or
claims of continuous monitoring.

Update this file whenever any of the following changes:

- merged application, workflow, deployment, environment, migration, data,
  recovery, security, or agent-operating contract;
- stage or production image, revision, Compose identity, route, or readiness;
- volume, database, object-store generation, manifest, backup, restore,
  activation, rollback, or retention posture;
- public authentication, OCR, embedding, external-side-effect, or search policy;
- incident, blocker, rejected option, operator approval, or next action.

Every update must:

1. refresh `Last verified` from observed evidence;
2. separate observed state, historical evidence, pending decisions, and plans;
3. state what changed, what was validated, what is blocked, and the exact next
   safe command or required approval;
4. preserve full digests and immutable identities where operationally relevant;
5. avoid secrets, document contents, credentials, and raw environment values;
6. update `LEGACY_VS_CURRENT_STATE.md` when the old-versus-current comparison
   changes materially;
7. record non-trivial operations in `~/.ai-audit`;
8. pass the handoff and documentation contracts before publication.

The fast PR contract requires this file to change with operationally meaningful
paths. Dated status pages remain historical and must not be rewritten to look
current.
