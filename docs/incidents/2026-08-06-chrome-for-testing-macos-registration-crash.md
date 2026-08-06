Status: Resolved local-tooling incident
Audience: Developer, CI maintainer
Owner: FlowDocs maintainers
Last verified: 2026-08-06
Canonical source: docs/incidents/2026-08-06-chrome-for-testing-macos-registration-crash.md
Supersedes: None

# Chrome for Testing macOS application-registration crash

## Recorded event

| Field | Supplied crash-report value |
| --- | --- |
| Process | Google Chrome for Testing |
| Version | 149.0.7827.55 (`7827.55`) |
| Architecture | ARM-64 native |
| Host | Mac14,6, Apple M2 Max |
| Operating system | macOS 26.5.2 (`25F84`) |
| Time | 2026-08-06 16:37:04.9025 +0530 |
| Incident identifier | `026DDBAE-BBDF-4AC6-8432-CF3DA4D0FF18` |
| Parent | `node` process 25445 |
| Responsible process | iTerm2 process 650 |
| Exception | `EXC_CRASH (SIGABRT)` |
| Termination | Signal 6, Abort trap 6 |
| Triggered thread | Main thread, `com.apple.main-thread` |

The decisive main-thread frames were:

```text
abort
___RegisterApplication_block_invoke
_RegisterApplication
TransformProcessType
ChromeMain
main
```

## Root-cause finding

The browser aborted while macOS HIServices was registering the process as a
GUI application. The failure occurred in application startup before project
HTML, Django, or a browser test page could execute. The parent was Node because
the local documentation renderer launched Chrome for Testing.

This is a local browser-tooling compatibility failure, not an application
crash, search failure, legal-page defect, stage incident, or data-custody
event. No repository data, Docker volume, stage service, or production service
was changed by the crash.

## Observed impact

The first local documentation-contract attempt could not render its Mermaid
batch because the renderer selected the GUI Chrome-for-Testing binary. It
reported zero rendered diagrams. Treat that command as failed; do not hide or
reinterpret its exit status.

Hosted Linux CI has its own installed browser setup and remains the canonical
merge gate. This incident only establishes the required local macOS posture.

## Resolution and verification

Use Playwright's installed `chrome-headless-shell` executable for local Mermaid
and documentation rendering on this host. Supply it through a temporary,
untracked Puppeteer configuration or `PUPPETEER_EXECUTABLE_PATH`; do not commit
machine-specific cache paths.

After switching to the local headless shell:

- both changed Mermaid sources validated successfully;
- both SVG exports completed successfully;
- the documentation contract rendered all diagrams and passed;
- the 16-case public-information Playwright matrix independently passed across
  desktop, laptop, tablet, and mobile projects.

## Prevention rule

On this macOS host, do not launch the GUI Google Chrome for Testing binary for
headless documentation or diagram work. Prefer the already-installed
`chrome-headless-shell`. If a future browser process again fails in
`_RegisterApplication` / `TransformProcessType`, classify the browser launch
separately from application behavior, preserve the command's real result, and
rerun only with a reviewed headless executable. Never weaken browser,
accessibility, or documentation assertions to work around this host-level
startup failure.
