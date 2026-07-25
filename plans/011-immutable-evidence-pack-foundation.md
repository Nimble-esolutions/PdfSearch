# Plan 011: Establish immutable evidence packs and reconciliation before changing databases

> **Executor instructions**: Build this without changing the public search API
> or replacing SQLite. The output must work on the current Docker volume and in
> a disposable object-storage bucket.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: none
- **Category**: migration / data-integrity
- **Planned at**: commit `b6c8363`, 2026-07-26

## Decision this plan establishes

The source of truth is not a FAISS file, JSON embedding array, filename, or
mutable Docker volume. Each searchable document revision becomes an immutable
evidence pack with a manifest, checksums, tool/config provenance, permissions,
and a Merkle/root digest. SQLite or PostgreSQL then holds a transactional
projection and lifecycle state; search indexes are derived artifacts.

This gives the project a safe low-cost path even if PostgreSQL is deferred:
SQLite can remain the write authority while verified read-only search snapshots
are built from packs. A later PostgreSQL or search-engine move becomes replay,
not blind copying.

## Current evidence

- `flowdocs/core/models.py:34-88` stores source file, text, chunks, embeddings,
  lifecycle, and category metadata but has no document-version or processing
  provenance identity.
- `flowdocs/core/utils.py:377-424` mutates a PDF row and rebuilds a folder
  index during processing.
- `flowdocs/core/maintenance.py:505-567` already creates manifests and uploads
  PDFs, FAISS files, and database snapshots through the opt-in vault.
- `flowdocs/core/artifact_vault.py:19-26` already supports content-addressed
  key validation and rejects mutable release aliases.

## Evidence-pack contract

Define a versioned JSON manifest containing document checksum, dataset and
legacy IDs, extractor/chunker/provider/model versions, chunk settings,
dimensions, access projection, and a list of checksummed PDF, text, page-map,
chunk, embedding, and index-input objects. Canonicalize JSON, page boundaries,
chunk ordering, and float serialization before hashing. A repeated build from
the same input/configuration must produce the same root; any changed input,
tool, model, or access projection produces a new root.

## Steps

1. Define manifest schema, canonicalization rules, and key builder.
2. Build a read-only pack generator for one existing PDF.
3. Add deterministic page-aware extraction and chunk serialization; preserve
   page numbers because current chunks do not provide reliable citation pages.
4. Validate every object before marking a manifest complete.
5. Store pack root and validation state in the current database without making
   packs required for existing search yet.
6. Add reconciliation comparing database rows, local files, packs, embeddings,
   indexes, and visibility projections.
7. Run it on the sanitized corpus and classify missing/orphaned/mismatched
   artifacts.
8. Publish verified packs to disposable object storage and restore one into a
   separate local volume.

## Reconciliation rules

Report, never silently repair: database rows without objects; objects without
projections; chunk/embedding count mismatch; model/dimension/config mismatch;
indexes containing wrong pack roots; visibility mismatches; or manifests whose
checksums fail.

## Verification and stop conditions

- Build one pack twice and compare roots byte-for-byte.
- Corrupt each object class and verify validation fails closed.
- Delete an index and rebuild it from the pack.
- Restore one pack and open/search its PDF.
- Run injected drift cases and confirm reconciliation catches every case.
- Existing Django, Compose, search, and browser suites remain unchanged.

Stop if page boundaries cannot be recovered, the active embedding model cannot
be identified, canonical serialization is non-deterministic, or access
projections cannot be reproduced. Do not resolve mismatches by choosing the
side with more files.

## Done criteria

- A verified pack reconstructs the PDF, text, chunks, embeddings, and index
  inputs without the original Docker volume.
- Reconciliation detects all injected drift classes.
- Existing search behavior and public response shape are unchanged.
- Plans 008 and 009 consume pack roots instead of copying opaque files.
