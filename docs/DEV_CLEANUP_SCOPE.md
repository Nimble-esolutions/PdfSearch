# `dev` Cleanup Scope

**Status:** Active audit record  
**Audit date:** 2026-07-25  
**Compared branch:** `origin/dev` at `a37b4d4`  
**Scope:** Read-only inventory; no cleanup deletion is included in this UI documentation change

## Root-cause summary

The current `dev` branch is functionally coherent for the Civic Workbench
change: no conflict markers were found in tracked application/docs text, the
public/admin templates exist, and the recent runtime smoke contract now matches
the canonical search routes. The remaining cleanup risk is documentation and
presentation drift, not an identified production outage.

## Findings and impact

| Priority | Finding | Evidence | Impact |
| --- | --- | --- | --- |
| P1 | Historical admin SRS still says `Planning` and describes a conflict marker/dead templates as active work | `docs/ADMIN_UI_REDESIGN_SRS.md`; its named `home.html`, `folder_files.html`, and `home_view` symbols are not present/referenced on `origin/dev` | Agents may repeat completed work, delete the wrong files, or treat stale phases as requirements |
| P1 | Public UI direction was previously spread across a design record, manuals, and implementation files without one lock contract | `docs/design/shakar2-civic-workbench.md`, README, SRS, templates/CSS | Future visual work can regress to a generic hero/chat pattern or lose source emphasis |
| P2 | Dashboard behavior still contains inline JavaScript and inline progress styles | `flowdocs/core/templates/dashboard.html` around the job drawer/progress renderer and destructive confirmation | Harder CSP adoption, testing, reuse, and visual consistency; not a current functional blocker |
| P2 | Legacy search cascade and new workbench tokens coexist | `flowdocs/core/static/main/css/search.css` and `civic-workbench.css` | Selector precedence and unused declarations make future visual repairs harder to reason about |
| P2 | Documentation has several historical/planning records with overlapping UI/data claims | `docs/UI_DATA_INTEGRATION_PLAN.md`, `docs/COMPLETION_PLAN.md`, historical release notes | Reviewers need clear current-vs-planned labels before changing operational/UI behavior |
| P3 | Admin dashboard remains a large template with repeated sections and role/action markup | `flowdocs/core/templates/dashboard.html` | Refactoring opportunity for partials and focused tests; deleting markup now would carry regression risk |

## Recommended sequence

1. Keep this contract and the historical SRS notice as the immediate guardrail.
2. Add focused browser coverage for admin role visibility, inline job progress,
   destructive confirmation, and mobile console navigation.
3. Extract dashboard job-drawer/progress behavior into a namespaced static JS
   module, then move progress presentation into tokenized CSS. Verify CSP and
   all admin smoke tests before changing templates.
4. Inventory `search.css` selectors against the current templates, remove only
   proven-unused rules in a separate cleanup PR, and keep a screenshot matrix.
5. Split dashboard sections into Django partials only after template coverage
   exists; do not combine this with route, authorization, or data changes.
6. Mark overlapping historical docs explicitly and link each to its current
   replacement.

## Explicitly not recommended now

Do not delete templates/routes/assets solely because a historical SRS called
them dead; prove references on the target branch first. Do not modify protected
data, search, embedding, OpenAI, RustFS, writer, scheduler, or authentication
code as part of this cleanup. Do not merge cleanup work into the design-lock
change without a separate diff and regression evidence.

## Audit commands

```bash
git fetch origin dev
git grep -n -E 'home_view|folder_files|home\.html' origin/dev -- ':!*.po' ':!*.json'
git grep -n -E 'Status:|Planning|TODO|FIXME|XXX' origin/dev -- docs README.md AGENTS.md
git show origin/dev:flowdocs/core/templates/dashboard.html | rg -n '<script|onclick|style='
git diff --check origin/dev...HEAD
```
