Status: Active
Audience: Recovery, Operator
Owner: FlowDocs maintainers
Last verified: 2026-08-02
Canonical source: docs/DATA_CUSTODY_AND_PROMOTION.md
Supersedes: None

# Data Custody and Promotion

## Current 2026-08-02 custody record

The legacy boundary remains authoritative and unchanged: service
sahakar-dev-frontend-dockerfile-1cubi5, volume prod_flowdocs, and read-only
application mount /app/flowdocs. The current migration inventory is 242 PDFs,
46 folders, 7 users, and 29 migrations. Do not confuse these values with the
older 2026-07-22 active-custody inventory documented below.

The v2 source generation legacy-20260802T085639Z-86288855 was cloned through
the explicit rebind operation into stage generation
clone-legacy-20260802T085639Z-86288855. The clone contains 416 verified objects
totalling 1,093,501,777 bytes. It was restored to quarantine and all 242 PDFs
were ultimately ready and indexed after bounded OCR processing. The generation
is not yet the signed active runtime generation; activation, first stage backup,
and isolated round trip remain separate gates.

Do not record the full manifest digest, object keys, or document contents in
this page. Use the receipt and the current status record for exact evidence:
[STATUS-2026-08-02.md](STATUS-2026-08-02.md).

## Boundary

The active application volume and the legacy volume are separate custody
domains. Legacy contains 242 PDFs and 45 FAISS files. After the 2026-07-22
reconciliation, active custody holds 253 PDF rows, 242 PDF files, 53 folders,
8 users, and 51 rebuilt FAISS indexes with 8,753 vectors. Only 6 PDF paths
overlap between legacy and active, and the databases diverge. No PDF contents
belong in an incident record.

The read-only `/mnt/legacy` mount is evidence/quarantine only. It is not a
permission to import or merge. RustFS is an isolated recovery vault; the
application supports explicit superadmin generation sync and staged pull, but
never overwrites active data during either operation.

## Required Lifecycle

1. **Quarantine:** preserve active and legacy sources unchanged; use separate
   read-only restore targets and record volume/snapshot identities.
2. **Inventory:** count database rows, PDF paths, FAISS files, and checksums
   without copying document contents.
3. **Conflict classification:** classify missing files, duplicate paths,
   divergent rows, orphan indexes, and uncertain matches. A path overlap is not
   proof of equivalent content.
4. **Staged restore:** construct a uniquely named disposable target from an
   explicit selection. Never write the active volume during analysis.
5. **FAISS fingerprint validation:** record file-level fingerprints and validate
   index load, expected dimensions/model metadata when available, and search
   behavior. See [`FAISS_COMPATIBILITY.md`](FAISS_COMPATIBILITY.md).
6. **Explicit promotion:** an operator records the selected source, conflict
    decisions, image/data compatibility, and approval before reconnecting or
    replacing active data.
7. **Compatibility check:** verify image, schema, embedding model, and index
    format compatibility before staging (`compatibility` module).
8. **Migration rehearsal:** dry-run migration against a disposable copy to
    detect schema conflicts before touching active data (`rehearsal` module).
9. **Sanitization:** remove sensitive or out-of-contract data before promotion
    (`sanitize` module).
10. **Activation journal:** record every activation step with audit trail
    (`activation_journal` module).
11. **Atomic pointer switch:** the `activate` module performs an atomic
    active-release pointer switch after all gates pass.

Legacy and active data must not be copied directly. `IMPORT_LEGACY_DATA` does
not replace this lifecycle and must not be treated as automatic promotion.

## RustFS Handling

Use the timestamped snapshots and checksums in bucket
`ai-sahakar-prod-flowdocs-data-volume` as recovery evidence. The bucket is
isolated from the application network; recovery is an operator-mediated restore
through the `restore_pipeline` and `restore_workspace` modules, not a runtime
read or automatic sync. The `object_store_capabilities` module detects and
verifies S3-compatible storage capabilities. Explicit superadmin generation sync
creates immutable dataset-scoped generations. The Workbench restore action
verifies authoritative inventory and prepares an isolated, validated,
rehearsed workspace. Promotion alone never claims that runtime bytes changed;
require the separately confirmed signed activation path and post-cutover
readiness evidence before treating a generation as active.

## Missing-PDF custody audit

`audit_missing_pdf_custody` is the read-only discovery path for database rows
whose referenced PDF is absent from local media. It does not restore files,
materialize or update a Vault profile, project generation records, extract an
archive, or change an authoritative pointer.

The command emits only numeric row IDs, HMAC-SHA-256 path/source tokens,
content hashes, byte counts, bounded postures, and these evidence classes:

- `metadata_consistent`: a historical manifest path matched
  internally and the content-addressed object HEAD metadata matched the
  manifest SHA-256 and size. This proves metadata consistency, not a fresh
  byte-for-byte object read;
- `manifest_reference_only`: the manifest retained hash/size evidence but the
  object could not be proved by a matching HEAD;
- `exact_manifest_archive`: a streamed archive member matched both the missing
  path internally and historical manifest SHA-256/size evidence;
- `path_only_candidate`: an archive member matched the path but lacks exact
  manifest-backed identity; and
- `no_match`: no reviewed source produced evidence.

Titles, stored paths, filenames, object keys, profile values, credential
values, and raw provider errors are never emitted. A path-only candidate is not
proof of the original bytes and must not be restored automatically.

Create a fresh operator-held correlation key with owner-only permissions. Keep
the same key only while results from separate archive runs need to be joined:

```bash
umask 077
openssl rand 48 > /tmp/pdf-custody-audit.key
```

Run the command in the maintenance role, where the existing locked profile and
approved server-side credential alias are already available:

```bash
python manage.py audit_missing_pdf_custody \
  --hmac-key-file /tmp/pdf-custody-audit.key
```

The Vault pass paginates every dataset-scoped generation manifest, validates
each candidate without projecting it into the control database, and performs
HEAD requests only for internally matched PDF paths. To inspect retained tar
archives, mount each exact archive read-only and repeat `--archive`:

```bash
python manage.py audit_missing_pdf_custody \
  --hmac-key-file /tmp/pdf-custody-audit.key \
  --archive /read-only-custody/retained-generation.tar.gz
```

Archive members are streamed without persistent extraction. The command caps
archive count and identity, physical bytes, member count, declared logical
bytes, total read work, compression ratio, candidate bytes, database rows,
media probes, manifest entries, generations, and evidence cardinality. Its
cooperative deadline starts before the database snapshot and is checked between
local work units. It is not a hard network timeout for an already-running
provider call. The deadline is checked for every manifest entry and immediately
before and after each object metadata probe. Its sequential tar reader requires
a canonical two-zero-block end, permits only zero padding through true EOF,
accepts ordinary POSIX/USTAR and exactly one CRC-valid gzip member, rejects
nonzero trailing/concatenated data plus PAX/GNU extended-name and sparse
records, and reports whether a bounded candidate begins with the PDF signature.
Other compression formats are rejected.

Every database, media, HMAC-key, and archive path component is opened through
no-follow directory descriptors. The HMAC key must be owned by the current
process user, have one link, have no group/other permissions, and retain the
same file identity and metadata through its bounded read. Database and archive
identity are checked again after scanning. SQLite is copied from the opened
descriptor into an immutable temporary snapshot; `-wal` or `-shm` siblings are
rejected, so operators must checkpoint the database before auditing. Stored,
manifest, and TAR paths must already be canonical relative POSIX paths. Exact
directory-entry spelling is verified with bounded, deadline-aware enumeration
at every component. Listing, pre-open metadata, the no-follow opened descriptor,
and post-open directory entry must retain one identity; noncanonical paths,
namespace swaps, case mismatches, and case-colliding evidence fail closed.

Review `complete`, `vault_posture`, `archive_posture`,
`vault_generation_counts`, per-source `archive_progress`, and `truncation`
before interpreting `no_match`. A partial, unavailable, time-limited, or
truncated scan never proves absence. Use `--skip-vault` only for a deliberate
archive-only pass. Redirect JSON to a protected operator evidence file if it
must be retained, then remove the temporary HMAC key when cross-run correlation
is complete.

`vault_generation_counts.returned` is only the bounded number returned to the
auditor. Treat it as a total only when `listing_complete` is true. `scanned`
and `verified` distinguish attempted manifests from manifests that passed the
existing validation contract.

`exact_manifest_archive` authorizes only isolated reconciliation review because
the streamed bytes matched manifest hash and size. Metadata-consistent HEAD
evidence alone is not enough to claim the bytes were freshly verified. Neither
result authorizes copying bytes into active custody, changing database rows, or
promoting a generation.

## Gates

Before promotion, pass link/path scan, Mermaid validation, Compose config,
`/livez`, `/readyz`, PDF count, FAISS count, and representative search. Retain
failed isolated targets and evidence until the recovery decision is closed.

## Current Versus Planned

Current: manual custody, generated inventory/generation manifests, conflict
classification, compatibility, migration rehearsal, sanitization, activation
journal, atomic pointer switch, global writer fencing, dataset registration,
writer lease, object-store capabilities, namespace, metrics, and a
CI-enforced operator path from authoritative generation through isolated
restore and signed runtime activation. Startup remains deliberately
fail-closed rather than automatically restoring.

Planned: immutable evidence-pack reconciliation, automatic reconciliation,
automatic cross-environment sync, and automated FAISS recovery. The restore
pipeline and activation journal provide the foundation for these; full
automation remains a future target.
