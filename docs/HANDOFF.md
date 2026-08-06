Status: Active, living handoff
Audience: Maintainer, Operator, Developer, AI agent, Reviewer
Owner: FlowDocs maintainers
Last verified: 2026-08-07
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

Evidence on this page was refreshed on 2026-08-07 (Asia/Kolkata). A live system
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
| Repository integration baseline | `dev` contains merge `51f0dd7` (PR #191) | PRs #185–#191 are merged; both themes, typed question-language responses, public information pages, macOS headless documentation rendering, Classic long-answer continuity, and the repository-truth audit are integrated |
| Search latency work | PR #192 is in review and is not deployed | Local gates pass (637 Django and 116 four-viewport browser tests). Pre-change stage baseline was 23,738 ms uncached and 12,910 ms on an immediate repeat; the PR moves exact caching before retrieval, reuses a signed-runtime corpus, caches provider-scoped query embeddings, adds phase telemetry, and removes Workbench reveal delay |
| Local development | Development Compose stack is currently stopped | Do not infer local data fitness from historical round-trip evidence; start and verify it when local runtime work resumes |
| Stage route | `https://2026.ai-sahakar.net/` returned HTTP 200 | Reachability only; `/readyz` remains authoritative |
| Stage services | Redis, web, and maintenance are running and healthy | Same Compose project and persistent volumes remain active |
| Stage application artifact | `ghcr.io/nimble-esolutions/pdfsearch/shakar-frontend@sha256:8649af368c2ba84f272942d7ab969055f0c2eaa502b4032f7bd52aa955cc52cc` | Web and maintenance are healthy on the same image ID and OCI revision `1067c054edd6a21a7881371ca0670e428dc4cc81` |
| Repository versus stage | Stage remains on OCI revision `1067c054edd6a21a7881371ca0670e428dc4cc81`; current `dev` is newer | Stage canaries prove the deployed revision only. PRs #187–#190 still require an integrated image certification and stage rollout before their behavior can be claimed live. |
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
| Data Operations | DataOps v3 is the active backup/import/restore product and workbench. VaultOps remains an internal compatibility API, control schema, selected maintenance surface, and signed activation/runtime bridge; it is not a second operator product. |
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
| Public search response contract | Exact standalone greetings/thanks/identity prompts are `small_talk`; document questions are `evidence_answer` or `no_evidence`; `language` follows the question (`en`/`mr`), not the selected UI; both themes consume the same typed JSON contract |

PR #186 corrected the legacy substring small-talk predicate that caused words
such as `updated` and `membership` to collide with `date` or `hi`. Matching is
now normalized and whole-query, responses are typed, and the backward-compatible
`answer` and `references` fields remain stable. Answer language is derived from
the question, validated against the returned script before caching, and repaired
once before failing explicitly.

The certified release passed the disposable runtime, activation, RustFS, MinIO,
browser, and package gates. Stage canaries then proved Classic and Workbench
rendering plus both mismatched-locale directions: Marathi input posted as English
returned Marathi/Devanagari evidence with three references, and English input
posted as Marathi returned English/Latin evidence with three references. The
active generation remained `dataops-import-legacy-20260802-86288855-stage-2026`
and indexing remained `1.0`.

## Recently completed work

| PR | Outcome |
| --- | --- |
| #176 | Simplified backup, import, restore, and recovery around DataOps v3 |
| #177 | Corrected public search routing across authorized documents |
| #178 | Unified operator-workbench truth, scale behavior, and stage image policy |
| #179 | Added pre-merge maintenance lifecycle certification |
| #180 | Added reviewed multi-file intake for up to 50 PDFs with queued processing |
| #182 | Recorded and closed the activated-runtime migration incident |
| #183 | Corrected Compose volume-ownership recovery guidance |
| #184 | Established this enforced living project handoff |
| #185 | Rebuilt the approved Classic public search as the primary view while preserving an isolated Knowledge Workbench secondary view |
| #186 | Corrected search-intent false positives and enforced question-derived English/Marathi answer language across both themes |
| #187 | Added a standalone, theme-consistent shell for Terms, Privacy, Disclaimer, Data Policy, and Cookie Policy |
| #188 | Made local macOS documentation rendering honor an explicit Puppeteer browser and select Playwright's matching headless shell instead of launching the crashing GUI Chrome-for-Testing app |
| #189 | Keeps the Classic composer reachable after long answers, safely formats structured responses, validates typed payloads and PDF references, and gates continuity across the viewport matrix |
| #190 | Updated the living handoff after the Classic continuity merge |

The stage volume warning is resolved. The three project-scoped volumes were
copied while quiescent, digest-verified, recreated with Docker Compose's
internal ownership labels, restored, re-verified, and restarted. No
`COMPOSE_PROJECT_NAME`, `external: true`, custom backup labels, or
Dokploy-specific volume workaround was added. A second Compose apply emitted
no ownership warning.

## Open decisions and next actions

There is no data-readiness or search-language blocker. Search latency remains a
measured release task until the current branch is reviewed, merged, deployed,
and canaried. Remaining work is:

1. **Search latency PR:** complete local/backend/browser/documentation gates,
   open a PR into `dev`, and merge only after required checks and review pass.
2. **Search latency stage canary:** after the merged image reaches stage, prove
   the unchanged signed generation and both themes; compare uncached/repeat
   duration, phase telemetry, corpus bytes/vectors, and web-worker RSS. Roll
   back the image if authorization, continuity, latency, or memory headroom
   regresses.
3. **Current integrated rollout:** certify an image from current `dev` containing
   merged PRs #187–#190, deploy stage web and maintenance without touching volumes, and
   canary all five public-information routes in both themes, both
   mismatched-locale answer directions, and Classic long-answer continuity.
4. **Future production project:** create and validate the dedicated 2026
   production Dokploy project only after explicit approval. Treat
   `/root/prod-2026.env` as prepared input, not deployment evidence.
5. **Production rehearsal:** before traffic changes, select an immutable image,
   validate rendered Compose and key-only environment posture, restore into
   isolated production-candidate volumes, run search/PDF/auth smoke checks, and
   record rollback image and generation.
6. **Production cutover:** remains out of scope until separately authorized.
   Do not change legacy service routing or `prod_flowdocs` while preparing it.
7. **Stage recovery retest:** run another manual backup and disposable restore
   only when recovery/data contracts change or when explicitly requested.
8. **Local development:** start the native development stack and rerun focused
   local recovery/search tests when a new implementation task requires it; the
   stack is intentionally stopped now.

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
- On macOS, documentation and browser automation must use Playwright's matching
  `chrome-headless-shell`; never pass the GUI Google Chrome for Testing app to
  Mermaid/Puppeteer. Honor an explicit `PUPPETEER_EXECUTABLE_PATH`, reject a
  missing path, and keep machine-specific browser-cache paths out of Git.
- Never classify conversational intent with substring matching. A small-talk
  fast path must match the complete normalized query, and regression tests must
  include domain words containing short conversational tokens.
- Both public themes must preserve the backend response `kind`; a friendly
  answer without evidence must not be presented as a document-backed answer.
- `overflow: auto` does not make a transcript scrollable unless every grid/flex
  ancestor gives it a bounded height and `min-height: 0`. Long-answer tests must
  prove transcript scroll ownership, a visible composer, protected sources,
  and a successful second query on portrait and short-landscape viewports.
- Public answer formatting must use allowlisted DOM nodes and text nodes. Never
  fix literal Markdown with unsanitized `innerHTML`; hostile HTML must remain
  inert text while headings, lists, and bold markers gain semantic structure.
- The page/session locale is a presentation preference, not proof of question
  language. Derive answer language from the question, expose it in the API and
  DOM, and never cache a provider response that fails the script check.
- Do not cache search results across mutable or unsigned data, authorization
  scopes, provider policies, models, languages, or answer-contract versions.
  A signed-runtime cache hit must still pass protected-reference and
  question-language handling at the response boundary.
- Do not simulate token streaming after a completed JSON response. Both public
  themes must format the completed answer immediately and announce only a
  concise completion status to assistive technology.
- Do not deploy a migration to an activated SQLite runtime unless the safe
  runtime migration classifier accepts it as recovery-backed and additive.
- Do not resurrect VaultOps as a parallel product surface. DataOps v3 replaced
  its operator workbench to reduce operator and code complexity. Do not claim
  that the package was deleted: its internal compatibility API, control schema,
  selected maintenance handlers, and signed activation/runtime bridge remain.
- Current operator documentation must say **Data protection** and **Search
  maintenance**. Historical VaultOps material must identify itself as
  historical/internal compatibility and link to the current DataOps v3 source.

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
