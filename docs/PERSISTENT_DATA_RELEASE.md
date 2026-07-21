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

## Required Manifest

Future release tooling must record:

```json
{
  "release_id": "2026-07-22T00:00:00Z",
  "git_sha": "...",
  "image_digest": "sha256:...",
  "schema_version": "...",
  "embedding_model": "...",
  "index_format": "...",
  "files": [{"path": "...", "sha256": "...", "bytes": 0}]
}
```

## Promotion Rules

1. Build a release in a staging directory or disposable volume.
2. Verify SQLite integrity and migration state.
3. Verify media references.
4. Load FAISS and Chroma indexes.
5. Run representative search tests.
6. Atomically promote the active release pointer.
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
