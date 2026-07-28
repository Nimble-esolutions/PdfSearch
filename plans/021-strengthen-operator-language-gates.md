# Plan 021: Make anti-slop CI prove rendered and accessible operator language

> **Executor instructions**: Keep checks deterministic and bounded. Do not add
> broad token allowlists merely to make a failing page pass.
>
> **Drift check (run first)**:
> `git diff --stat f7d0536..HEAD -- scripts/ci/validate_operator_language.py scripts/ci/test_operator_language.py browser_tests/helpers/operator-language.ts browser_tests/operations-cockpit.spec.ts browser_tests/vault-workbench.spec.ts .github/workflows/docker-build.yml`

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: LOW
- **Depends on**: 018, 019, 020
- **Category**: tests
- **Planned at**: commit `f7d0536`, 2026-07-28
- **Implementation**: DONE — source fixtures and rendered English/Marathi
  visible-text and accessibility-name scans enforce the boundary.

## Why this matters

The current source validator detects only direct interpolation patterns and
underscore replacement. The browser helper walks visible DOM text but does not
audit accessible names/descriptions, every Workbench section, ordinary-user
role boundaries, or expanded evidence redaction. Green CI therefore does not
prove several acceptance requirements it claims to enforce.

## Current state

- `scripts/ci/validate_operator_language.py:31-66` scans templates and Python
  source with narrow regular expressions.
- `scripts/ci/test_operator_language.py:8-28` tests patterns directly but does
  not build failing/pass fixture templates.
- `browser_tests/helpers/operator-language.ts:5-20` ignores closed details and
  code elements while scanning visible text only.
- The workflow triggers pull-request validation only when the PR base is
  `dev`; dependent PRs #101-#103 have no automatic PR check rollup.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Validator tests | `python3 -m unittest scripts.ci.test_operator_language` | all pass |
| Source validator | `python3 scripts/ci/validate_operator_language.py` | exit 0 |
| Browser gate | `npx playwright test browser_tests/operations-cockpit.spec.ts browser_tests/vault-workbench.spec.ts` | all configured projects pass |
| Workflow syntax | `actionlint .github/workflows/*.yml` | exit 0 |

## Scope

**In scope**:

- operator-language source validator and fixtures
- shared Playwright audit helper
- Dashboard and all eight Workbench sections
- CI trigger/check design for dependent PRs

**Out of scope**:

- general-purpose linting unrelated to machine evidence
- hiding legitimate IDs, timestamps, hashes, or filenames
- weakening permissions to make role tests easier

## Steps

### Step 1: Test validator behavior with fixtures

Create temporary fixture templates/source for prohibited direct fields, raw
state values, raw error summaries, underscore formatting, and approved
component usage. Require a stable diagnostic per violation. Add a registry
inventory check from Plan 018.

**Verify**: each bad fixture fails and the approved fixture passes.

### Step 2: Audit the accessibility tree

Extend the helper to inspect accessible names/descriptions for headings,
buttons, links, form controls, statuses, alerts, and details summaries. Keep a
narrow documented allowlist for bounded identifiers. Verify raw codes are
absent while details are closed and exact/redacted codes appear only after
authorized expansion.

**Verify**: a deliberately machine-tokenized `aria-label` fixture fails.

### Step 3: Cover roles, sections, states, locales, and widths

Exercise Dashboard and every Workbench section at 320, 768, 1024, and 1440
widths in English and Marathi. Cover healthy, warning, disabled, running,
failed, stale, unknown, and recovery-blocked states. Assert public and ordinary
users cannot receive technical diagnostics; superadmins can reveal bounded
evidence.

**Verify**: Playwright/Axe suite passes and reports each section/state matrix
case by name.

### Step 4: Give every stacked PR automatic hosted evidence

Adjust the workflow trigger or add a reusable validation workflow so PRs
targeting approved stack branches receive a check attached to their head SHA.
Keep publishing restricted to `dev`. Avoid a branch-name pattern that lets an
untrusted fork publish.

**Verify**: actionlint passes; a test PR to a stack branch gets the validation
check while the release job remains skipped.

## Test plan

- Negative/positive source fixtures.
- Visible DOM and accessibility-name token fixtures.
- Closed/open technical details.
- Public, ordinary, admin, and superadmin boundaries.
- Full locale, state, section, and viewport matrix.
- Hosted check attachment for child PR heads.

## Done criteria

- [ ] Every prohibited source fixture fails.
- [ ] Accessible names cannot leak machine tokens.
- [ ] Every Dashboard/Workbench matrix cell is exercised.
- [ ] Raw exception text is absent even after expansion.
- [ ] PRs #101-#103 equivalents receive automatic head-SHA checks.
- [ ] Publishing remains possible only from `dev` under existing controls.

## STOP conditions

- Accessibility-tree inspection requires a broad token allowlist.
- CI trigger changes could allow package publication from non-`dev` refs.
- Fixtures require production data or credentials.

## Maintenance notes

Keep token allowlists close to the exact semantic use (UUID, digest, filename)
and require a regression fixture for every new exception.
