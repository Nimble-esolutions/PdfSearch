Status: Historical
Audience: Operator
Owner: FlowDocs maintainers
Last verified: 2026-07-22
Canonical source: docs/INDEX.md
Supersedes: None

> Historical and quarantined. This file describes an unrelated static landing
> page deployment, not the FlowDocs Django application. It contains no current
> deployment instructions. Use [`DEPLOYMENT_GUIDE.md`](DEPLOYMENT_GUIDE.md) for
> the actual Dokploy Compose application.

# Dokploy Static Deployment Notes

## Preserved Context

The original document discussed a Dokploy Static application for CC and RCS
Maharashtra landing pages, an Nginx static document root, and files under a
separate landing-page repository. Those claims do not describe this repository's
Compose application and are retained only to explain why the document is not a
valid FlowDocs deployment guide.

## Current FlowDocs Replacement

FlowDocs is deployed as a Dokploy Compose application using
[`docker-compose.yml`](docker-compose.yml), with the web service on container
port `8000`, immutable application code under `/app/flowdocs`, and mutable data
under `/app/data`. Follow [`DEPLOYMENT_GUIDE.md`](DEPLOYMENT_GUIDE.md) and
[`docs/OPERATIONS_RUNBOOK.md`](docs/OPERATIONS_RUNBOOK.md).
