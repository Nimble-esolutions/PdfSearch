# Plan 001: Maintain the Registrar domain and visual identity contract

> **Executor instructions**: This is an active decision record, not an
> implementation backlog. Do not redesign the product identity from this file.
> Re-open it only when an official source, approved asset, or human product
> decision changes.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: direction / product contract
- **Planned at**: commit `f742b59`, 2026-07-26
- **Roadmap status**: DONE

## Drift check

Re-open the official links below and compare
`docs/design/AI_SAHAKAR_UI_CONTRACT.md` before changing vocabulary, identity,
or institutional claims. If an official page is unavailable or contradictory,
stop and request human confirmation.

## Confirmed institutional context

The Commissioner for Cooperation and Registrar of Co-operative Societies (CC/RCS) operates within Maharashtra's Department of Cooperation, Marketing and Textiles. The department administers the Maharashtra Cooperative Societies Act, 1960 and Rules, and covers registration, member rights, society property and funds, management, audit, inquiry, inspection, disputes, liquidation, offences, and review/revision work. Source: [Maharashtra e-Governance overview](https://mahasahakar.maharashtra.gov.in/en/e-governance-overview/).

The operating functions include policy and legislation, registration, bye-law approval and amendment, audit, inspection, supervision, supersession/administrators, liquidators, and winding up. Source: [Objectives and functions](https://mahasahakar.maharashtra.gov.in/en/about-department/objectives-functions/).

The organization is hierarchical: commissioner/registrar, additional registrars, deputy and assistant registrars, audit functions, and divisional/regional offices. Source: [Administrative setup](https://mahasahakar.maharashtra.gov.in/en/about-department/administrative-setup/).

Public service language includes registration, bye-law amendment, licensing/renewal, deemed conveyance, and e-QJ applications, appeals, and revisions. Source: [Maharashtra services](https://mahasahakar.maharashtra.gov.in/en/services/).

## Product implications

- Use official terms such as `society`, `registration`, `bye-laws`, `audit`, `inspection`, `inquiry`, `appeal`, `revision`, `liquidation`, `provenance`, and `document readiness`.
- Treat search as a public information service, not a generic AI chat product. Search results need source, category, document status, language, and retrieval timestamp cues.
- Treat admin work as a case/document operations console. Operators need truthfully labelled states, auditability, safe retry, and clear ownership.
- Do not hardcode changing counts or office details in UI copy; the official overview itself contains time-sensitive scale figures.
- Marathi support is a first-class responsive requirement. Avoid translating legal or operational labels ad hoc in templates; use the existing i18n path.

## Identity and logo rules

- The CC/RCS identity mark and official government marks must be sourced from official or permission-safe assets and used with correct clear space and alt text.
- The current Hallmark header and CC mark are the visual baseline. Do not replace them with a speculative internet logo.
- The Nimble mark remains a restrained technology-partner credit in the footer, not the institutional identity.
- Any candidate logo requires source URL, usage/licence evidence, image dimensions, contrast check, and operator approval before adoption.

## Verification and maintenance

- Public English and Marathi copy uses the same institutional meaning.
- No changing office count, document count, hours, or availability claim is
  hardcoded without a dated official source.
- Official marks retain source/licence evidence and accessible alternative
  text.
- Reviewers reject robot branding, invented government imagery, tricolour
  decoration, and partner marks that compete with the department identity.
- Revalidate this record annually or when the department publishes a new
  identity/service standard.
