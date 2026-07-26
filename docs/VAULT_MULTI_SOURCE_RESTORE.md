# Vault Multi-Source Restore

The vault restore path prepares a verified runtime generation; it never
activates it. Runtime activation remains a separate, disabled-by-default
control-plane operation.

## Trust chain

Every restore verifies this chain in order:

1. The selected server-side profile is enabled and its non-secret fingerprint
   still matches the job.
2. `registration.json` matches the profile dataset, application identifier,
   production source, and supported manifest schema range.
3. `authoritative.json` identifies an exact dataset-scoped manifest and binds
   its SHA-256 digest. A pinned candidate may be restored, but it is not
   represented as authoritative.
4. The immutable manifest matches the generation, dataset, source, application
   release, image digest, migration evidence, embedding model, and FAISS
   evidence.
5. Each object is dataset-scoped and its size and SHA-256 metadata match before
   download. Downloaded bytes are hashed again before the file is committed.

The newest generation is never inferred to be authoritative.

## Profiles and credentials

V1 accepts environment credentials or a deployed credential alias only.
`VaultConnectionProfile` stores endpoint posture and the alias, never access or
secret keys.

Configure an alias as:

```env
VAULT_CREDENTIAL_ALIASES=production-readonly=PROD_RESTORE
PROD_RESTORE_ACCESS_KEY=<server secret>
PROD_RESTORE_SECRET_KEY=<server secret>
```

The endpoint must be an exact origin in `VAULT_ALLOWED_S3_ENDPOINTS`. HTTPS is
required by default. DNS is resolved before the S3 client is created, and
loopback, private, link-local, multicast, reserved, carrier-grade NAT, and
metadata-network destinations are rejected. Local integration environments may
set both `VAULT_ALLOW_HTTP_S3_ENDPOINTS=1` and
`VAULT_BLOCK_PRIVATE_S3_ENDPOINTS=0`; production must not.

Browser-entered credentials remain unsupported and
`VAULT_UI_SECRET_ENTRY_ENABLED=0`.

## Restore phases

```text
verified inventory
  -> planned workspace
  -> resumable quarantine download
  -> byte, path, SQLite, and FAISS validation
  -> prepared copy
  -> mandatory sanitization for production-derived non-production data
  -> isolated migration rehearsal
  -> immutable activation-ready runtime workspace
```

Quarantine is immutable after validation. Sanitization and migration rehearsal
operate on separate copies, so failed preparation does not alter downloaded
evidence. Static files from a generation remain custody data and are marked
`custody_only`; release assets continue to come from the application image.

Object-level checkpoints live in the stable control database. A transient
object-store failure leaves the workspace `download_paused`; retry verifies and
reuses completed objects. Path traversal, symlinks, hard links, case
collisions, native executable suffixes, digest mismatch, and database or FAISS
incoherence fail closed.

## Enabling restore

Keep both switches disabled during initial deployment:

```env
VAULT_RESTORE_ENABLED=0
VAULT_ADMIN_MUTATIONS_ENABLED=0
VAULT_RESTORE_REQUIRE_SANITIZATION=1
VAULT_ALLOWED_S3_ENDPOINTS=https://approved-vault.example
```

After a read-only profile probe and disposable restore rehearsal, enable
`VAULT_RESTORE_ENABLED`. Operator job creation additionally requires
`VAULT_ADMIN_MUTATIONS_ENABLED`. These switches do not enable runtime
activation.

The web and maintenance services must share:

- `/app/data-control/control.sqlite3` for jobs, checkpoints, audit, and
  projections;
- `/app/data/restore-quarantine` for immutable downloaded evidence; and
- `/app/data/runtime-generations` for prepared runtime generations.

## Safe failures

Control-plane records store typed codes, not raw provider or database errors.
Important codes include:

- `profile_fingerprint_changed`
- `vault_endpoint_not_allowlisted`
- `authoritative_manifest_digest_mismatch`
- `manifest_path_unsafe`
- `generation_object_digest_mismatch`
- `restore_object_read_failed`
- `restore_object_digest_mismatch`
- `generation_compatibility_failed`
- `production_restore_sanitization_required`
- `restore_sanitization_validation_failed`
- `migration_rehearsal_failed`
- `restore_capacity_bytes_insufficient`

A failed or cancelled restore does not move the remote pointer or the runtime
pointer. Removing a failed workspace is a separately authorized retention
operation; do not delete quarantine during incident analysis.

## Verification

Before enabling an approved profile:

1. Run Django and vaultops unit tests.
2. Parse every Compose configuration.
3. Exercise registration, pointer, manifest, and object verification against
   disposable MinIO/RustFS.
4. Interrupt one object download and prove checkpoint reuse.
5. Restore production-derived data into non-production and prove the quarantine
   retains original bytes while the prepared database has sanitized users and
   no sessions.
6. Prove a rehearsal failure leaves the runtime generation unprepared.
7. Confirm no activation intent or runtime pointer is created.
