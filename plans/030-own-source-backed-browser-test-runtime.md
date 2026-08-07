# Plan 030: Make browser tests own a source-backed runtime

> **Executor instructions**: Browser acceptance must exercise the checked-out
> source revision. Do not make a test pass by reusing an arbitrary service on
> port 8000 or by launching the macOS GUI Chrome-for-Testing application.
>
> **Drift check (run first)**:
> `git diff --stat d13e415..HEAD -- package.json playwright.config.ts scripts/ci browser_tests docs/incidents/2026-08-06-chrome-for-testing-macos-registration-crash.md`

## Status

- **Priority**: P1
- **Effort**: S/M
- **Risk**: LOW — test orchestration only; no runtime application behavior
- **Depends on**: none
- **Category**: tests, developer experience
- **Planned at**: commit `d13e415`, 2026-08-07

## Why this matters

Playwright currently defaults to `http://localhost:8000` without owning that
service. A stale container or another checkout can therefore produce convincing
passes or failures against the wrong JavaScript, templates, and database. The
repository already has a safe matching headless shell; the missing piece is one
discoverable command that starts, identifies, tests, and stops its own source
runtime.

## Current state

- `playwright.config.ts` falls back to port 8000 and has no `webServer` owner.
- `package.json` exposes no useful browser-test script.
- CI wrappers set `PLAYWRIGHT_BASE_URL` explicitly and must remain supported.
- The macOS incident runbook correctly requires Playwright's matching
  `chrome-headless-shell`, not the crashing GUI test browser.

## Scope

**In scope**: a repository-owned local test launcher, unique loopback port,
explicit test settings, readiness/revision sentinel, cleanup on success and
failure, package scripts, and focused harness tests/documentation.

**Out of scope**: replacing Compose CI, changing production startup, installing
a second browser framework, or adding a long-lived development server.

## Steps

1. Add one portable launcher that reserves a unique loopback port, starts the
   checked-out Django source with explicit test-only settings, waits for a
   revision-bearing health response, exports `PLAYWRIGHT_BASE_URL`, and always
   terminates the child process.
2. Make Playwright refuse the implicit port-8000 fallback for repository-owned
   search suites. Preserve explicit external URLs for Compose and stage canaries.
3. Add `test:browser:search` and `test:browser:search:headed` scripts. The normal
   command must select Playwright's installed matching headless shell on macOS.
4. Assert the served revision sentinel before behavioral tests. Add a harness
   failure test for occupied ports, startup failure, and revision mismatch.
5. Document the one-command workflow and the distinction between source-backed
   acceptance and an explicitly targeted deployed canary.

## Done criteria

- [ ] The default search-browser command cannot test an unrelated port-8000 service.
- [ ] Started child processes are cleaned up after pass, failure, or interruption.
- [ ] CI's explicit `PLAYWRIGHT_BASE_URL` workflow still passes.
- [ ] macOS uses matching headless shell and does not reproduce the HIServices crash.
- [ ] No new runtime dependency or environment variable is added.

## STOP conditions

- A proposed launcher requires disabling the existing test-settings safety checks.
- The revision sentinel would expose a secret or mutable deployment identifier.
- CI and local behavior cannot share the same behavioral specs.
