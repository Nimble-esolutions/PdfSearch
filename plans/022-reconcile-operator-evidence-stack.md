# Plan 022: Reconcile roadmap and stacked-PR merge readiness

> **Executor instructions**: This is a governance and evidence plan. Do not
> merge any PR without explicit operator confirmation.
>
> **Drift check (run first)**:
> `git diff --stat f7d0536..HEAD -- plans/README.md plans/015-workbench-readiness-and-remediation-ux.md plans/016-bound-maintenance-read-model-and-browser-gates.md plans/017-human-centred-operator-evidence.md .github/pull_request_template.md`

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: 018, 019, 020, 021
- **Category**: docs
- **Planned at**: commit `f7d0536`, 2026-07-28
- **Implementation**: DONE — roadmap, documentation, rules, skill, checks, and
  stacked-PR merge evidence were reconciled.

## Why this matters

The roadmap omits Plan 017 from its status table, still says Plan 016 lacks the
end-to-end lifecycle that PR #102 now supplies, and describes Plan 017 as
implemented despite the registry, redaction, and Marathi gaps found in this
audit. The open stack is correctly ordered and mergeable, but #100-#102 remain
draft, none has a review decision, and child PRs have no automatic check
rollup. Documentation must not imply completion or merge readiness prematurely.

## Current state

- `plans/README.md:3` is reviewed only through commit `6c9a262`.
- `plans/README.md:19-36` has no row for Plan 017.
- `plans/README.md:161-164` says Plan 016 is awaiting the lifecycle now
  implemented and verified by PR #102.
- `plans/017-human-centred-operator-evidence.md:3` says implemented.
- Open ordered stack: `dev → #100 → #101 → #102 → #103`; ancestry and
  mergeability were verified at `f7d0536`.
- #100-#102 are draft, #103 is open/non-draft, all four have no review
  decision. Only #100 has a native PR-triggered check; #103 has successful
  manual run 30380776260 attached to its head SHA but not its PR check rollup.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| PR inventory | `gh pr list -R Nimble-esolutions/PdfSearch --state open --json number,isDraft,baseRefName,headRefName,mergeable,mergeStateStatus,reviewDecision,statusCheckRollup` | exact stack visible |
| Ancestry | `git merge-base --is-ancestor origin/feat/vault-recovery-maintenance-e2e origin/feat/operator-evidence-anti-slop` | exit 0 |
| Docs contract | `python3 -m unittest scripts.ci.test_docs_contract && python3 scripts/ci/docs_contract.py` | all pass |
| Cleanliness | `git status --short` | empty |

## Scope

**In scope**:

- plans 015-022 and `plans/README.md`
- PR descriptions/checklists and readiness labels
- audit ledger evidence

**Out of scope**:

- merging any PR
- restacking before a parent merge
- changing application behavior
- deleting historical failed CI runs or remote branches

## Steps

### Step 1: Reconcile plan status truthfully

Mark Plan 016 DONE only after linking the exact PR #102 lifecycle evidence.
Add Plans 017-022 to the index. Keep Plan 017 RECONCILE/BLOCKED until Plans
018-021 are complete and verified. Update the reviewed commit and dependency
graph.

**Verify**: the index has one row per plan file and no implemented claim
contradicts an open remediation plan.

### Step 2: Align PR readiness

Keep descendants draft or explicitly mark them blocked until their parents and
required remediation are review-ready. Add exact hosted/local evidence without
claiming that manual workflow dispatch is a native PR status check.

**Verify**: each PR description names its parent, current head evidence,
remaining blockers, migration impact, rollback, and “do not merge” condition.

### Step 3: Hand off the merge sequence

Record the current parent tips and tree hashes. Recommend bottom-up review.
After any squash/rebase merge, use the repository stacked-PR workflow with
backup refs, unchanged-tree proof, and force-with-lease; do not perform that
rewrite in advance.

**Verify**: all open descendants remain exact ancestors of their immediate
parent before handoff.

## Test plan

- One-to-one plan index/file reconciliation.
- PR base/head/ancestry verification.
- Native check versus manual-run distinction.
- Documentation contract.

## Done criteria

- [ ] Plan 016 evidence is reconciled.
- [ ] Plans 017-022 appear in the roadmap with truthful status.
- [ ] No PR is represented as merge-ready without review and required checks.
- [ ] Parent tips/tree hashes and restack instructions are recorded.
- [ ] No merge or premature history rewrite occurs.

## STOP conditions

- A parent PR merges while reconciliation is in progress; immediately switch
  to the stacked-PR recovery workflow.
- Any branch tree differs from the reviewed evidence.
- The operator requests a merge before remediation and review are green; report
  the blockers rather than merging.

## Maintenance notes

Manual dispatch proves the commit passed CI; it does not populate the PR's
required-check rollup. Keep those facts separate in reviews and release notes.
