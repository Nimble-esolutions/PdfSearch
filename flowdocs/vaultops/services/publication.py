import base64
import hashlib
import json
import re
import secrets
import time
from datetime import datetime, timezone as datetime_timezone
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.artifact_vault import ArtifactVault, object_metadata_value
from core.media_quarantine import (
    build_unauthorized_missing_attestation,
    build_unavailable_attestation,
    validate_unavailable_attestation,
)
from core.global_writer import (
    acquire_global_writer,
    release_global_writer,
    renew_global_writer,
    validate_writer_for_publication,
)
from core.namespace import KeyBuilder
from core.object_store_capabilities import probe_capabilities
from core.registration import update_authoritative_pointer
from vaultops.models import (
    ArtifactGeneration,
    ArtifactValidation,
    SourceSnapshot,
    VaultJobStep,
)
from vaultops.services.audit import append_event
from vaultops.services.lifecycle import transition_generation_vault_state


class PublicationError(RuntimeError):
    reason_code = "publication_failed"

    def __init__(self, reason_code=None):
        self.reason_code = reason_code or self.reason_code
        super().__init__(self.reason_code)


class PublicationCancelled(PublicationError):
    reason_code = "publication_cancelled"


class PromotionError(PublicationError):
    reason_code = "promotion_failed"


def _canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _error_code(exc):
    response = getattr(exc, "response", {}) or {}
    error = response.get("Error", {}) or {}
    return str(error.get("Code", ""))


def _is_missing(exc):
    return _error_code(exc) in {"404", "NoSuchKey", "NotFound"}


def _read_json_object(vault, key, *, missing_allowed=False):
    try:
        response = vault.client.get_object(Bucket=vault.config.bucket, Key=key)
    except Exception as exc:
        if missing_allowed and _is_missing(exc):
            return None
        raise PublicationError("vault_control_read_failed") from exc
    try:
        data = response["Body"].read()
        payload = json.loads(data)
    except (KeyError, TypeError, ValueError, UnicodeDecodeError) as exc:
        raise PublicationError("vault_control_malformed") from exc
    if not isinstance(payload, dict):
        raise PublicationError("vault_control_malformed")
    digest = hashlib.sha256(data).hexdigest()
    stored_digest = object_metadata_value(response.get("Metadata"), "sha256", "")
    if stored_digest and stored_digest != digest:
        raise PublicationError("vault_control_digest_mismatch")
    payload["_etag"] = response.get("ETag", "")
    payload["_digest"] = digest
    return payload


def _ensure_registration(vault, *, dataset_id, production_source_id):
    keys = KeyBuilder(dataset_id)
    key = keys.control_registration()
    existing = _read_json_object(vault, key, missing_allowed=True)
    if existing is None:
        registration = {
            "dataset_id": dataset_id,
            "registration_version": 1,
            "manifest_schema_range": {"min": 1, "max": 1},
            "app_identifier": "pdfsearch",
            "production_source_id": production_source_id,
            "initial_instance_id": settings.ENV_IDENTITY.instance_id,
            "registration_nonce": secrets.token_hex(16),
            "created_at": datetime.now(datetime_timezone.utc).isoformat(),
        }
        data = _canonical_bytes(registration)
        try:
            vault.client.put_object(
                Bucket=vault.config.bucket,
                Key=key,
                Body=data,
                ContentType="application/json",
                Metadata={"sha256": hashlib.sha256(data).hexdigest()},
                IfNoneMatch="*",
            )
        except Exception:
            pass
        existing = _read_json_object(vault, key)
    if (
        existing.get("dataset_id") != dataset_id
        or existing.get("registration_version") != 1
        or existing.get("app_identifier") != "pdfsearch"
        or existing.get("production_source_id") != production_source_id
        or existing.get("manifest_schema_range") != {"min": 1, "max": 1}
    ):
        raise PublicationError("registration_identity_mismatch")
    return existing


def _head(vault, key):
    try:
        return vault.client.head_object(Bucket=vault.config.bucket, Key=key)
    except Exception as exc:
        if _is_missing(exc):
            return None
        raise PublicationError("object_head_failed") from exc


def _verify_head(response, *, digest, size):
    if response is None:
        raise PublicationError("published_object_missing")
    if (
        object_metadata_value(response.get("Metadata"), "sha256") != digest
        or int(response.get("ContentLength", -1)) != size
    ):
        raise PublicationError("published_object_digest_mismatch")


def _put_or_reuse(
    vault,
    *,
    key,
    path,
    digest,
    size,
    content_type,
    content_md5,
):
    existing = _head(vault, key)
    if existing is not None:
        _verify_head(existing, digest=digest, size=size)
        return "reused"
    try:
        with path.open("rb") as stream:
            vault.client.put_object(
                Bucket=vault.config.bucket,
                Key=key,
                Body=stream,
                ContentLength=size,
                ContentType=content_type,
                ContentMD5=content_md5,
                Metadata={"sha256": digest, "immutable": "true"},
                IfNoneMatch="*",
            )
    except Exception:
        existing = _head(vault, key)
        _verify_head(existing, digest=digest, size=size)
        return "reused_after_race"
    _verify_head(_head(vault, key), digest=digest, size=size)
    return "uploaded"


def _content_type(path):
    if path.suffix.lower() == ".json":
        return "application/json"
    if path.suffix.lower() == ".pdf":
        return "application/pdf"
    if path.suffix.lower() in {".sqlite", ".sqlite3", ".index"}:
        return "application/octet-stream"
    return "application/octet-stream"


def _object_key(keys, record):
    digest = record["sha256"]
    path = Path(record["path"])
    category = record["path"].split("/", 1)[0]
    if record["path"] == "db.sqlite3":
        return keys.blob_db(digest), "database"
    if category == "media" and path.suffix.lower() == ".pdf":
        return keys.blob_pdf(digest), "pdf"
    if category == "faiss_indexes":
        return keys.blob_faiss(digest), "faiss"
    extension = path.suffix.lower().lstrip(".")
    return keys.content_blob(category, digest, extension), category


def _load_snapshot_files(snapshot):
    workspace = Path(snapshot.workspace_path)
    if workspace.is_symlink():
        raise PublicationError("snapshot_path_unsafe")
    root = workspace.resolve()
    evidence_path = root / "snapshot-evidence.json"
    if evidence_path.is_symlink():
        raise PublicationError("snapshot_path_unsafe")
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PublicationError("snapshot_evidence_unavailable") from exc
    if evidence.get("snapshot_id") != str(snapshot.public_id):
        raise PublicationError("snapshot_identity_mismatch")
    files = evidence.get("files")
    if not isinstance(files, list):
        raise PublicationError("snapshot_evidence_malformed")
    resolved = []
    for record in files:
        relative = Path(record.get("path", ""))
        if relative.is_absolute() or ".." in relative.parts:
            raise PublicationError("snapshot_path_unsafe")
        candidate = root / relative
        if candidate.is_symlink():
            raise PublicationError("snapshot_path_unsafe")
        path = candidate.resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise PublicationError("snapshot_path_unsafe") from exc
        if not path.is_file():
            raise PublicationError("snapshot_artifact_missing")
        if path.stat().st_nlink != 1:
            raise PublicationError("snapshot_hardlink_rejected")
        expected_digest = record.get("sha256", "")
        expected_size = record.get("size_bytes")
        if (
            not isinstance(expected_digest, str)
            or len(expected_digest) != 64
            or not isinstance(expected_size, int)
            or expected_size < 0
        ):
            raise PublicationError("snapshot_evidence_malformed")
        sha256 = hashlib.sha256()
        md5 = hashlib.md5(usedforsecurity=False)
        actual_size = 0
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                sha256.update(chunk)
                md5.update(chunk)
                actual_size += len(chunk)
        if actual_size != expected_size or sha256.hexdigest() != expected_digest:
            raise PublicationError("snapshot_artifact_digest_mismatch")
        resolved.append(
            {
                **record,
                "local_path": path,
                "content_md5": base64.b64encode(md5.digest()).decode("ascii"),
            }
        )
    return evidence, resolved


def _validate_snapshot_media_evidence(evidence):
    inventory = evidence.get("inventory")
    faiss = evidence.get("faiss")
    if not isinstance(inventory, dict) or not isinstance(faiss, dict):
        raise PublicationError("snapshot_media_evidence_invalid")
    counts = inventory.get("counts")
    pdfs = inventory.get("pdfs")
    if pdfs is None and isinstance(counts, dict) and counts.get("pdf_rows") == 0:
        pdfs = []
    if (
        not isinstance(pdfs, list)
        or not isinstance(counts, dict)
        or counts.get("pdf_rows") != len(pdfs)
    ):
        raise PublicationError("snapshot_media_evidence_invalid")
    valid_lifecycles = {
        "uploaded",
        "processing",
        "ready",
        "deprecated",
        "archived",
        "unavailable",
    }
    if any(
        not isinstance(item, dict)
        or not isinstance(item.get("metadata"), dict)
        or item["metadata"].get("lifecycle") not in valid_lifecycles
        for item in pdfs
    ):
        raise PublicationError("snapshot_media_evidence_invalid")
    try:
        inventory_unavailable = validate_unavailable_attestation(
            inventory.get(
                "unavailable_documents",
                build_unavailable_attestation(()),
            )
        )
        faiss_unavailable = validate_unavailable_attestation(
            faiss.get(
                "unavailable_documents",
                build_unavailable_attestation(()),
            )
        )
        rebuilt_unavailable = build_unavailable_attestation(
            (
                {
                    "id": item["db_id"],
                    "lifecycle": item["metadata"].get("lifecycle"),
                    "storage_key_status": item["metadata"].get(
                        "storage_key_status"
                    ),
                    "storage_key_token_sha256": item["metadata"].get(
                        "storage_key_token_sha256"
                    ),
                    "expected_sha256": item["metadata"].get(
                        "media_expected_sha256"
                    ),
                    "expected_size": item["metadata"].get(
                        "media_expected_size"
                    ),
                    "prior_lifecycle": item["metadata"].get(
                        "media_prior_lifecycle"
                    ),
                }
                for item in pdfs
                if isinstance(item, dict)
                and isinstance(item.get("metadata"), dict)
                and item["metadata"].get("lifecycle") == "unavailable"
            )
        )
        inventory_unauthorized = inventory.get(
            "unauthorized_missing_documents",
            build_unauthorized_missing_attestation(()),
        )
        rebuilt_unauthorized = build_unauthorized_missing_attestation(
            (
                {
                    "id": item["db_id"],
                    "lifecycle": item["metadata"].get("lifecycle") or "legacy",
                    "file_status": item.get("file_status"),
                    "storage_key_token_sha256": item["metadata"].get(
                        "storage_key_token_sha256"
                    ),
                }
                for item in pdfs
                if isinstance(item, dict)
                and isinstance(item.get("metadata"), dict)
                and item["metadata"].get("lifecycle") != "unavailable"
                and item.get("exists") is not True
            )
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise PublicationError("snapshot_media_evidence_invalid") from exc
    if (
        inventory_unavailable != faiss_unavailable
        or inventory_unavailable != rebuilt_unavailable
    ):
        raise PublicationError("snapshot_unavailable_attestation_mismatch")
    if (
        inventory_unauthorized != rebuilt_unauthorized
        or rebuilt_unauthorized["count"]
    ):
        raise PublicationError("snapshot_media_missing")
    unauthorized = [
        item
        for item in pdfs
        if not isinstance(item, dict)
        or not isinstance(item.get("metadata"), dict)
        or (
            item["metadata"].get("lifecycle") != "unavailable"
            and item.get("exists") is not True
        )
    ]
    if unauthorized:
        raise PublicationError("snapshot_media_missing")


def _validate_faiss_reconciliation_evidence(evidence):
    reconciliation = evidence.get("faiss_reconciliation")
    if reconciliation in (None, {}):
        return {}
    inventory = evidence.get("inventory", {})
    files = inventory.get("faiss", {}).get("files", [])
    folders = reconciliation.get("folders")
    if (
        reconciliation.get("schema") != 1
        or reconciliation.get("source") != "stored_embeddings"
        or not isinstance(folders, dict)
        or not isinstance(files, list)
    ):
        raise PublicationError("snapshot_faiss_reconciliation_invalid")
    indexed = {}
    inventory_folder_ids = set()
    for item in files:
        path = item.get("path") if isinstance(item, dict) else None
        matched = (
            re.fullmatch(r"faiss_indexes/folder_([0-9]+)\.index", path)
            if isinstance(path, str)
            else None
        )
        if (
            matched is None
            or path in indexed
            or str(int(matched.group(1))) != matched.group(1)
        ):
            raise PublicationError("snapshot_faiss_reconciliation_invalid")
        indexed[path] = item
        inventory_folder_ids.add(matched.group(1))
    vector_total = 0
    vector_bytes = 0
    pdf_total = 0
    nonremoved_folder_ids = set()
    removed_folder_ids = set()
    for folder_id, record in folders.items():
        if (
            not isinstance(folder_id, str)
            or not folder_id.isdigit()
            or str(int(folder_id)) != folder_id
            or not isinstance(record, dict)
        ):
            raise PublicationError("snapshot_faiss_reconciliation_invalid")
        disposition = record.get("disposition")
        if disposition == "removed":
            if (
                record.get("vector_count") != 0
                or record.get("pdf_count") != 0
                or folder_id in inventory_folder_ids
            ):
                raise PublicationError(
                    "snapshot_faiss_reconciliation_invalid"
                )
            removed_folder_ids.add(folder_id)
            continue
        item = indexed.get(f"faiss_indexes/folder_{folder_id}.index")
        metadata = item.get("faiss", {}) if isinstance(item, dict) else {}
        pdf_count = record.get("pdf_count")
        vector_count = record.get("vector_count")
        dimensions = record.get("dimensions")
        digest = record.get("sha256")
        if (
            disposition not in {"copied", "rebuilt"}
            or not isinstance(item, dict)
            or not isinstance(pdf_count, int)
            or isinstance(pdf_count, bool)
            or pdf_count <= 0
            or not isinstance(vector_count, int)
            or isinstance(vector_count, bool)
            or vector_count <= 0
            or not isinstance(dimensions, int)
            or isinstance(dimensions, bool)
            or dimensions <= 0
            or dimensions > settings.VAULT_SNAPSHOT_FAISS_MAX_DIMENSIONS
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or item.get("sha256") != digest
            or metadata.get("loadable") is not True
            or metadata.get("vector_count") != vector_count
            or metadata.get("dimensions") != dimensions
        ):
            raise PublicationError("snapshot_faiss_reconciliation_invalid")
        nonremoved_folder_ids.add(folder_id)
        pdf_total += pdf_count
        vector_total += vector_count
        vector_bytes += vector_count * dimensions * 4
    if (
        nonremoved_folder_ids != inventory_folder_ids
        or nonremoved_folder_ids & removed_folder_ids
        or vector_total != reconciliation.get("vector_count")
        or vector_bytes != reconciliation.get("vector_bytes")
        or pdf_total != reconciliation.get("pdf_count")
        or not isinstance(reconciliation.get("source_json_bytes"), int)
        or isinstance(reconciliation.get("source_json_bytes"), bool)
        or reconciliation["source_json_bytes"] < 0
        or reconciliation["source_json_bytes"]
        > settings.VAULT_SNAPSHOT_FAISS_MAX_SOURCE_BYTES
    ):
        raise PublicationError("snapshot_faiss_reconciliation_invalid")
    return reconciliation


def _manifest_key_for_generation(dataset_id, generation_id):
    return KeyBuilder(dataset_id).generation_manifest(generation_id)


def _put_manifest_immutable(vault, *, key, data, digest):
    existing = _read_json_object(vault, key, missing_allowed=True)
    if existing is not None:
        if existing.get("_digest") != digest:
            raise PublicationError("generation_manifest_conflict")
        return "reused"
    try:
        vault.client.put_object(
            Bucket=vault.config.bucket,
            Key=key,
            Body=data,
            ContentType="application/json",
            Metadata={"sha256": digest, "immutable": "true"},
            IfNoneMatch="*",
        )
    except Exception:
        existing = _read_json_object(vault, key)
        if existing.get("_digest") != digest:
            raise PublicationError("generation_manifest_conflict")
        return "reused_after_race"
    existing = _read_json_object(vault, key)
    if existing.get("_digest") != digest:
        raise PublicationError("generation_manifest_digest_mismatch")
    return "uploaded"


def publish_snapshot_candidate(
    *,
    snapshot,
    profile,
    job,
    vault=None,
    cancellation_check=None,
    heartbeat=None,
):
    """Publish immutable objects and manifest; never move the pointer."""
    if snapshot.state != SourceSnapshot.State.FINALIZED:
        raise PublicationError("snapshot_not_finalized")
    cancellation_check = cancellation_check or (lambda: False)
    heartbeat = heartbeat or (lambda **kwargs: None)
    vault = vault or ArtifactVault()
    identity = settings.ENV_IDENTITY
    if not settings.VAULT_SYNC_ENABLED:
        raise PublicationError("vault_sync_disabled")
    if not identity.is_authoritative_writer:
        raise PublicationError("writer_environment_required")
    if profile.dataset_id != identity.dataset_id or profile.fingerprint == "":
        raise PublicationError("profile_identity_mismatch")
    if job.profile_fingerprint != profile.fingerprint:
        raise PublicationError("profile_fingerprint_changed")

    evidence, files = _load_snapshot_files(snapshot)
    _validate_snapshot_media_evidence(evidence)
    faiss_reconciliation = _validate_faiss_reconciliation_evidence(evidence)
    capabilities = probe_capabilities(
        vault, deployment_id=identity.deployment_id
    )
    if not capabilities.authoritative_publication_allowed:
        raise PublicationError("conditional_operations_unsupported")
    _ensure_registration(
        vault,
        dataset_id=identity.dataset_id,
        production_source_id=identity.production_source_id,
    )
    writer = acquire_global_writer(
        vault,
        identity.dataset_id,
        production_source_id=identity.production_source_id,
        instance_id=identity.instance_id,
        deployment_id=identity.deployment_id,
        replica_id=identity.replica_id,
        app_release=identity.app_release_version,
        image_digest=identity.app_image_digest,
    )
    keys = KeyBuilder(identity.dataset_id)
    generation_id = job.generation_id or (
        timezone.now().strftime("gen-%Y%m%dT%H%M%S-")
        + snapshot.snapshot_digest[:12]
        + "-"
        + secrets.token_hex(3)
    )
    if not job.generation_id:
        job.generation_id = generation_id
        job.manifest_digest = ""
        job.save(
            update_fields=["generation_id", "manifest_digest", "updated_at"]
        )
    step, _ = VaultJobStep.objects.get_or_create(
        job=job,
        phase="uploading",
        defaults={"status": VaultJobStep.Status.RUNNING},
    )
    uploaded_files = []
    last_renewal = time.monotonic()
    manifest_published = False
    try:
        checkpoint = step.checkpoint if isinstance(step.checkpoint, dict) else {}
        object_checkpoints = checkpoint.get("objects", {})
        for index, record in enumerate(files, start=1):
            if cancellation_check():
                raise PublicationCancelled("publication_cancelled")
            if time.monotonic() - last_renewal >= 120:
                writer = renew_global_writer(
                    vault,
                    identity.dataset_id,
                    writer_record=writer,
                )
                last_renewal = time.monotonic()
            key, artifact_type = _object_key(keys, record)
            result = _put_or_reuse(
                vault,
                key=key,
                path=record["local_path"],
                digest=record["sha256"],
                size=int(record["size_bytes"]),
                content_type=_content_type(record["local_path"]),
                content_md5=record["content_md5"],
            )
            uploaded = {
                "path": record["path"],
                "bytes": int(record["size_bytes"]),
                "sha256": record["sha256"],
                "object_key": key,
                "artifact_type": artifact_type,
            }
            uploaded_files.append(uploaded)
            object_checkpoints[key] = {
                "sha256": record["sha256"],
                "bytes": int(record["size_bytes"]),
                "result": result,
            }
            step.status = VaultJobStep.Status.RUNNING
            step.checkpoint = {"objects": object_checkpoints}
            step.completed_objects = index
            step.completed_bytes = sum(
                int(item["bytes"]) for item in uploaded_files
            )
            step.save(
                update_fields=[
                    "status",
                    "checkpoint",
                    "completed_objects",
                    "completed_bytes",
                    "updated_at",
                ]
            )
            heartbeat(
                phase="uploading",
                progress={
                    "objects_completed": index,
                    "objects_total": len(files),
                    "bytes_completed": step.completed_bytes,
                },
            )

        for record in uploaded_files:
            _verify_head(
                _head(vault, record["object_key"]),
                digest=record["sha256"],
                size=record["bytes"],
            )
        writer = validate_writer_for_publication(
            vault,
            identity.dataset_id,
            writer_record=writer,
            production_source_id=identity.production_source_id,
            instance_id=identity.instance_id,
            expected_epoch=writer["writer_epoch"],
        )
        inventory = evidence.get("inventory", {})
        unavailable_documents = validate_unavailable_attestation(
            evidence.get("faiss", {}).get(
                "unavailable_documents",
                build_unavailable_attestation(()),
            )
        )
        manifest = {
            "release_id": generation_id,
            "manifest_version": 1,
            "read_only": True,
            "source": "consistent-local-snapshot",
            "profile_fingerprint": profile.fingerprint,
            "dataset_id": identity.dataset_id,
            "production_source_id": identity.production_source_id,
            "app_release": identity.app_release_version,
            "image_digest": identity.app_image_digest,
            "writer_epoch": writer["writer_epoch"],
            "snapshot_id": str(snapshot.public_id),
            "snapshot_digest": snapshot.snapshot_digest,
            "source_epoch": snapshot.included_epoch,
            "schema": inventory.get("schema", {}),
            "database": inventory.get("database", {}),
            "counts": inventory.get("counts", {}),
            "embedding_index": {
                **inventory.get("embedding_index", {}),
                "model": settings.OPENAI_EMBED_MODEL,
            },
            "faiss": inventory.get("faiss", {}),
            "faiss_reconciliation": faiss_reconciliation,
            "pdf_storage": inventory.get("pdf_storage", {}),
            "chroma": inventory.get("chroma", {}),
            "static": inventory.get("static", {}),
            "unavailable_documents": unavailable_documents,
            "files": uploaded_files,
        }
        manifest_data = _canonical_bytes(manifest)
        manifest_digest = hashlib.sha256(manifest_data).hexdigest()
        manifest_key = _manifest_key_for_generation(
            identity.dataset_id, generation_id
        )
        _put_manifest_immutable(
            vault,
            key=manifest_key,
            data=manifest_data,
            digest=manifest_digest,
        )
        manifest_published = True
        generation, _ = ArtifactGeneration.objects.update_or_create(
            profile=profile,
            dataset_id=identity.dataset_id,
            generation_id=generation_id,
            defaults={
                "manifest_digest": manifest_digest,
                "manifest": manifest,
                "vault_state": ArtifactGeneration.VaultState.CANDIDATE,
                "runtime_state": ArtifactGeneration.RuntimeState.UNKNOWN,
                "local_presence": ArtifactGeneration.LocalPresence.UNKNOWN,
                "deployment_id": identity.deployment_id,
                "source": "active-sync",
                "observed_at": timezone.now(),
            },
        )
        ArtifactValidation.objects.create(
            generation=generation,
            validation_type="publication",
            status=ArtifactValidation.Status.PASSED,
            manifest_digest=manifest_digest,
            validator_version="active-sync/v1",
            reason_codes=[],
            evidence={
                "files_verified": len(uploaded_files),
                "writer_epoch": writer["writer_epoch"],
                "snapshot_digest": snapshot.snapshot_digest,
                "unavailable_documents": unavailable_documents,
                "faiss_reconciliation": faiss_reconciliation,
            },
            expires_at=timezone.now()
            + timezone.timedelta(
                seconds=getattr(
                    settings, "VAULT_VALIDATION_MAX_AGE_SECONDS", 1800
                )
            ),
            validated_by_id=job.requested_by_id,
            validated_by_name=job.requested_by_name,
        )
        job.manifest_digest = manifest_digest
        job.save(update_fields=["manifest_digest", "updated_at"])
        step.status = VaultJobStep.Status.COMPLETED
        step.checkpoint = {
            **step.checkpoint,
            "manifest_key": manifest_key,
            "manifest_digest": manifest_digest,
        }
        step.finished_at = timezone.now()
        step.save(
            update_fields=[
                "status",
                "checkpoint",
                "finished_at",
                "updated_at",
            ]
        )
        append_event(
            action="generation_candidate_published",
            result="succeeded",
            correlation_id=job.correlation_id,
            actor_id=job.requested_by_id,
            actor_name=job.requested_by_name,
            job_public_id=job.public_id,
            after_state={
                "vault_state": ArtifactGeneration.VaultState.CANDIDATE
            },
            evidence={
                "dataset_id": identity.dataset_id,
                "generation_id": generation_id,
                "manifest_digest": manifest_digest,
            },
        )
        if cancellation_check():
            append_event(
                action="publication_cancelled_after_manifest",
                result="candidate_preserved",
                correlation_id=job.correlation_id,
                job_public_id=job.public_id,
                reason_code="candidate_immutable",
                evidence={"generation_id": generation_id},
            )
        return generation
    except Exception:
        if manifest_published:
            step.checkpoint = {
                **step.checkpoint,
                "candidate_preserved": True,
            }
            step.save(update_fields=["checkpoint", "updated_at"])
        raise
    finally:
        try:
            release_global_writer(
                vault,
                identity.dataset_id,
                writer_record=writer,
            )
        except Exception:
            pass


def _strict_pointer(vault, dataset_id):
    return _read_json_object(
        vault,
        KeyBuilder(dataset_id).control_authoritative(),
        missing_allowed=True,
    )


def promote_candidate(
    *,
    generation,
    job,
    profile,
    confirmed=False,
    vault=None,
):
    """CAS-promote a validated candidate; publication never calls this implicitly."""
    if not confirmed:
        raise PromotionError("typed_confirmation_required")
    if generation.vault_state != ArtifactGeneration.VaultState.CANDIDATE:
        raise PromotionError("generation_not_candidate")
    if job.profile_fingerprint != profile.fingerprint:
        raise PromotionError("profile_fingerprint_changed")
    vault = vault or ArtifactVault()
    identity = settings.ENV_IDENTITY
    capabilities = probe_capabilities(
        vault, deployment_id=identity.deployment_id
    )
    if not capabilities.authoritative_publication_allowed:
        raise PromotionError("conditional_operations_unsupported")
    validation = generation.validations.filter(
        status=ArtifactValidation.Status.PASSED,
        manifest_digest=generation.manifest_digest,
        expires_at__gt=timezone.now(),
    ).order_by("-created_at").first()
    if validation is None:
        raise PromotionError("fresh_validation_required")
    manifest_key = _manifest_key_for_generation(
        generation.dataset_id, generation.generation_id
    )
    manifest = _read_json_object(vault, manifest_key)
    if manifest.get("_digest") != generation.manifest_digest:
        raise PromotionError("manifest_digest_mismatch")
    _ensure_registration(
        vault,
        dataset_id=generation.dataset_id,
        production_source_id=identity.production_source_id,
    )
    writer = acquire_global_writer(
        vault,
        generation.dataset_id,
        production_source_id=identity.production_source_id,
        instance_id=identity.instance_id,
        deployment_id=identity.deployment_id,
        replica_id=identity.replica_id,
        app_release=identity.app_release_version,
        image_digest=identity.app_image_digest,
    )
    try:
        writer = validate_writer_for_publication(
            vault,
            generation.dataset_id,
            writer_record=writer,
            production_source_id=identity.production_source_id,
            instance_id=identity.instance_id,
            expected_epoch=writer["writer_epoch"],
        )
        current = _strict_pointer(vault, generation.dataset_id)
        pointer = update_authoritative_pointer(
            vault,
            generation.dataset_id,
            generation_id=generation.generation_id,
            manifest_object_key=manifest_key,
            manifest_sha256=generation.manifest_digest,
            writer_epoch=writer["writer_epoch"],
            production_source_id=identity.production_source_id,
            app_release=identity.app_release_version,
            image_digest=identity.app_image_digest,
            database_schema=(
                generation.manifest.get("database", {})
                .get("migrations", {})
                .get("latest", "")
            ),
            previous_generation_id=(
                current.get("generation_id", "") if current else ""
            ),
            expected_pointer_etag=current.get("_etag") if current else None,
            writer_token_hash=writer.get("owner_token_hash", ""),
            instance_id=identity.instance_id,
        )
        with transaction.atomic(using="control"):
            prior = list(
                ArtifactGeneration.objects.select_for_update().filter(
                    profile=profile,
                    dataset_id=generation.dataset_id,
                    vault_state=ArtifactGeneration.VaultState.AUTHORITATIVE,
                ).exclude(pk=generation.pk)
            )
            for previous in prior:
                transition_generation_vault_state(
                    previous,
                    ArtifactGeneration.VaultState.CANDIDATE,
                    correlation_id=job.correlation_id,
                    reason_code="superseded_by_pointer_cas",
                )
            generation = transition_generation_vault_state(
                generation,
                ArtifactGeneration.VaultState.AUTHORITATIVE,
                correlation_id=job.correlation_id,
                actor_id=job.requested_by_id,
                actor_name=job.requested_by_name,
                reason_code="pointer_cas_succeeded",
            )
        return generation, pointer
    finally:
        try:
            release_global_writer(
                vault,
                generation.dataset_id,
                writer_record=writer,
            )
        except Exception:
            pass
