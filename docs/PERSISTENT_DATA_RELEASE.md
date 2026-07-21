Status: Active
Audience: Recovery
Owner: FlowDocs maintainers
Last verified: 2026-07-22
Canonical source: docs/PERSISTENT_DATA_RELEASE.md
Supersedes: None

# Persistent Data Release Contract

## Scope

A data release is the compatible set of mutable application state used by one
application image. It includes:

- SQLite database;
- uploaded media;
- FAISS indexes;
- Chroma data;
- static files;
- backup metadata and checksums.

## Current Minimum Release Record

The current implementation has no generated persistent-data manifest or active
release pointer. For every production release, the operator must retain a
record containing:

- Git SHA;
- tested application image digest;
- Compose file path and hash;
- actual volume identity mounted at `/app/data`;
- backup reference and timestamp;
- migration result and `/readyz` result;
- database, media, FAISS, Chroma, and representative search verification;
- previous known-good image and backup references.

The release workflow publishes image evidence, SBOM, and provenance, but it does
not create this data record.

## Future Artifact Manifest

Future release tooling must record:

```json
{
  "release_id": "<operator-or-tool-generated-id>",
  "git_sha": "...",
  "image_digest": "sha256:...",
  "schema_version": "<when-defined>",
  "embedding_model": "<when-defined>",
  "index_format": "<when-defined>",
  "files": [{"path": "<relative-path>", "sha256": "<checksum>", "bytes": 0}]
}
```

This JSON is a proposed future artifact contract. Do not claim that it is
currently emitted, complete, or validated by CI.

## Promotion Rules

1. Build a release in a staging directory or disposable volume.
2. Verify SQLite integrity and migration state.
3. Verify media references.
4. Load FAISS and Chroma indexes.
5. Run representative search tests.
6. Promote only after the operator release record is complete. An atomic active
   release pointer is a future capability, not a current runtime behavior.
7. Retain the previous known-good release.

## Rollback Rules

Rollback must select a compatible application image and data release together.
Never restore only the image when a migration or embedding/index format changed.

## Retention

Garbage collection must protect:

- active release;
- previous known-good release;
- all releases referenced by backup records;
- releases under incident investigation.
