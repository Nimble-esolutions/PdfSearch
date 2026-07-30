import gzip
import hashlib
import io
import json
import os
import sqlite3
import tarfile
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.test import SimpleTestCase

from core.custody_audit import (
    CustodyAuditError,
    _open_entry_no_follow,
    _require_exact_entry,
    _scan_vault,
    audit_missing_pdf_custody,
    read_hmac_key_file,
)


class FakeVault:
    def __init__(self, *, fail_head=False):
        self.fail_head = fail_head
        self.head_calls = []

    def head(self, key, expected_sha256=None):
        self.head_calls.append((key, expected_sha256))
        if self.fail_head:
            raise RuntimeError("provider leaked a private bucket and filename")
        return SimpleNamespace(
            sha256=expected_sha256,
            size=len(b"%PDF-1.7\nrecovered"),
        )


class MissingPdfCustodyAuditTests(SimpleTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(os.path.realpath(self.temporary.name))
        self.database = self.root / "db.sqlite3"
        self.media = self.root / "media"
        self.media.mkdir()
        connection = sqlite3.connect(self.database)
        connection.execute(
            """
            CREATE TABLE core_pdffile (
                id INTEGER PRIMARY KEY,
                title TEXT,
                file TEXT,
                file_path TEXT
            )
            """
        )
        connection.executemany(
            "INSERT INTO core_pdffile(id, title, file, file_path) VALUES (?, ?, ?, ?)",
            [
                (1, "Sensitive missing title", "pdfs/private-name.pdf", None),
                (2, "Present title", "pdfs/present.pdf", None),
            ],
        )
        connection.commit()
        connection.close()
        (self.media / "pdfs").mkdir()
        (self.media / "pdfs" / "present.pdf").write_bytes(b"%PDF-present")
        self.key = b"k" * 32
        self.payload = b"%PDF-1.7\nrecovered"
        self.digest = hashlib.sha256(self.payload).hexdigest()
        self.profile = SimpleNamespace(dataset_id="dataset")

    def tearDown(self):
        self.temporary.cleanup()

    def _verified(self):
        return SimpleNamespace(
            manifest={
                "files": [
                    {
                        "path": "media/pdfs/private-name.pdf",
                        "bytes": len(self.payload),
                        "sha256": self.digest,
                        "object_key": (
                            f"datasets/dataset/blobs/pdfs/sha256/{self.digest}.pdf"
                        ),
                    }
                ]
            }
        )

    def _list_ids(
        self,
        vault,
        profile,
        *,
        max_pages,
        max_items,
        include_status,
    ):
        self.assertIsInstance(vault, FakeVault)
        self.assertEqual(profile, self.profile)
        self.assertEqual(max_pages, 7)
        self.assertEqual(max_items, 10_000)
        self.assertTrue(include_status)
        return ["generation-with-sensitive-operator-label"], True

    def _verify(self, vault, profile, *, generation_id, verify_objects):
        self.assertFalse(verify_objects)
        self.assertEqual(generation_id, "generation-with-sensitive-operator-label")
        return self._verified()

    def _archive(self):
        archive = self.root / "retained-private-backup.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            info = tarfile.TarInfo("volume/media/pdfs/private-name.pdf")
            info.size = len(self.payload)
            bundle.addfile(info, io.BytesIO(self.payload))
        self._canonicalize_archive(archive, compressed=True)
        return archive

    def _canonicalize_archive(self, archive, *, compressed=False):
        raw = gzip.decompress(archive.read_bytes()) if compressed else archive.read_bytes()
        offset = 0
        while raw[offset : offset + 512] != b"\0" * 512:
            header = raw[offset : offset + 512]
            size_field = header[124:136].rstrip(b"\0 ").lstrip(b" ")
            size = int(size_field or b"0", 8)
            offset += 512 + size + ((-size) % 512)
        canonical = raw[: offset + 1024]
        archive.write_bytes(gzip.compress(canonical) if compressed else canonical)

    def _changed_metadata(self, metadata):
        values = {
            name: getattr(metadata, name)
            for name in (
                "st_dev",
                "st_ino",
                "st_mode",
                "st_uid",
                "st_nlink",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
            )
        }
        values["st_ctime_ns"] += 1
        return SimpleNamespace(**values)

    def _extended_archive(self, *, sparse=False):
        archive = self.root / ("sparse.tar" if sparse else "pax.tar")
        archive_format = tarfile.USTAR_FORMAT if sparse else tarfile.PAX_FORMAT
        with tarfile.open(archive, "w", format=archive_format) as bundle:
            info = tarfile.TarInfo("volume/media/pdfs/private-name.pdf")
            info.size = 0
            if sparse:
                info.type = b"S"
            else:
                info.pax_headers = {"comment": "extended metadata"}
            bundle.addfile(info, io.BytesIO())
        self._canonicalize_archive(archive)
        return archive

    def test_exact_vault_and_archive_evidence_is_redacted(self):
        vault = FakeVault()
        result = audit_missing_pdf_custody(
            database=self.database,
            media_root=self.media,
            hmac_key=self.key,
            archives=[self._archive()],
            vault=vault,
            profile=self.profile,
            list_generation_ids=self._list_ids,
            verify_generation=self._verify,
            max_pages=7,
        )

        self.assertEqual(result["missing_reference_count"], 1)
        self.assertEqual(
            result["vault_generation_counts"],
            {
                "returned": 1,
                "scanned": 1,
                "verified": 1,
                "listing_complete": True,
            },
        )
        classes = {
            item["evidence_class"]
            for item in result["results"][0]["evidence"]
        }
        self.assertEqual(
            classes,
            {"metadata_consistent", "exact_manifest_archive"},
        )
        self.assertEqual(
            result["evidence_counts"]["metadata_consistent"],
            1,
        )
        rendered = json.dumps(result)
        for forbidden in (
            "private-name.pdf",
            "Sensitive missing title",
            "retained-private-backup",
            "generation-with-sensitive-operator-label",
            "object_key",
        ):
            self.assertNotIn(forbidden, rendered)
        self.assertIn(self.digest, rendered)
        self.assertRegex(
            result["results"][0]["path_token"],
            r"^hmac-sha256:[0-9a-f]{64}$",
        )

    def test_provider_failure_is_collapsed_to_safe_manifest_reference(self):
        vault = FakeVault(fail_head=True)
        result = audit_missing_pdf_custody(
            database=self.database,
            media_root=self.media,
            hmac_key=self.key,
            vault=vault,
            profile=self.profile,
            list_generation_ids=self._list_ids,
            verify_generation=self._verify,
            max_pages=7,
        )

        evidence = result["results"][0]["evidence"][0]
        self.assertEqual(evidence["evidence_class"], "manifest_reference_only")
        self.assertEqual(evidence["object_posture"], "unavailable")
        self.assertNotIn("provider leaked", json.dumps(result))

    def test_unavailable_generation_is_counted_without_identifier_leakage(self):
        vault = FakeVault()

        def list_ids(vault_arg, profile_arg, **kwargs):
            del vault_arg, profile_arg, kwargs
            return ["verified-generation", "private-failed-generation"], True

        def verify(vault_arg, profile_arg, *, generation_id, verify_objects):
            del vault_arg, profile_arg, verify_objects
            if generation_id == "private-failed-generation":
                raise RuntimeError("private provider error")
            return self._verified()

        result = audit_missing_pdf_custody(
            database=self.database,
            media_root=self.media,
            hmac_key=self.key,
            vault=vault,
            profile=self.profile,
            list_generation_ids=list_ids,
            verify_generation=verify,
        )
        self.assertEqual(result["vault_posture"], "partial")
        self.assertEqual(
            result["vault_generation_counts"],
            {
                "returned": 2,
                "scanned": 2,
                "verified": 1,
                "listing_complete": True,
            },
        )
        rendered = json.dumps(result)
        self.assertNotIn("private-failed-generation", rendered)
        self.assertNotIn("private provider error", rendered)

    def test_archive_without_manifest_is_path_only_candidate(self):
        result = audit_missing_pdf_custody(
            database=self.database,
            media_root=self.media,
            hmac_key=self.key,
            archives=[self._archive()],
        )
        evidence = result["results"][0]["evidence"][0]
        self.assertEqual(evidence["evidence_class"], "path_only_candidate")
        self.assertEqual(evidence["pdf_header"], "valid")

    def test_archive_candidate_size_cap_avoids_reading_body(self):
        result = audit_missing_pdf_custody(
            database=self.database,
            media_root=self.media,
            hmac_key=self.key,
            archives=[self._archive()],
            max_candidate_bytes=5,
        )
        evidence = result["results"][0]["evidence"][0]
        self.assertEqual(evidence["candidate_posture"], "size_limit_exceeded")
        self.assertIsNone(evidence["sha256"])

    def test_archive_logical_byte_cap_fails_closed(self):
        result = audit_missing_pdf_custody(
            database=self.database,
            media_root=self.media,
            hmac_key=self.key,
            archives=[self._archive()],
            max_archive_logical_bytes=5,
        )
        self.assertFalse(result["complete"])
        self.assertEqual(result["archive_posture"], "partial")
        self.assertEqual(
            result["archive_progress"][0]["posture"],
            "archive_logical_byte_limit_exceeded",
        )

    def test_pax_and_sparse_archives_are_rejected_before_candidate_read(self):
        for archive in (
            self._extended_archive(),
            self._extended_archive(sparse=True),
        ):
            result = audit_missing_pdf_custody(
                database=self.database,
                media_root=self.media,
                hmac_key=self.key,
                archives=[archive],
            )
            progress = result["archive_progress"][0]
            self.assertEqual(
                progress["posture"],
                "archive_extended_header_rejected",
            )
            self.assertEqual(progress["candidates_hashed"], 0)

    def test_gzip_expansion_ratio_is_bounded(self):
        archive = self.root / "compressible.tar.gz"
        payload = b"%PDF-" + (b"0" * (1024 * 1024))
        with tarfile.open(archive, "w:gz") as bundle:
            info = tarfile.TarInfo("volume/media/pdfs/private-name.pdf")
            info.size = len(payload)
            bundle.addfile(info, io.BytesIO(payload))
        self._canonicalize_archive(archive, compressed=True)
        result = audit_missing_pdf_custody(
            database=self.database,
            media_root=self.media,
            hmac_key=self.key,
            archives=[archive],
            max_compression_ratio=2,
        )
        self.assertEqual(
            result["archive_progress"][0]["posture"],
            "archive_compression_ratio_exceeded",
        )

    def test_physical_and_read_work_caps_are_truthful(self):
        archive = self.root / "physical.tar"
        with tarfile.open(archive, "w") as bundle:
            info = tarfile.TarInfo("volume/media/pdfs/private-name.pdf")
            info.size = len(self.payload)
            bundle.addfile(info, io.BytesIO(self.payload))
        self._canonicalize_archive(archive)
        physical = audit_missing_pdf_custody(
            database=self.database,
            media_root=self.media,
            hmac_key=self.key,
            archives=[archive],
            max_archive_physical_bytes=512,
        )
        self.assertEqual(
            physical["archive_progress"][0]["posture"],
            "archive_physical_byte_limit_exceeded",
        )
        work = audit_missing_pdf_custody(
            database=self.database,
            media_root=self.media,
            hmac_key=self.key,
            archives=[archive],
            max_archive_read_bytes=512,
        )
        self.assertEqual(
            work["archive_progress"][0]["posture"],
            "archive_read_limit_exceeded",
        )

    def test_exact_case_matching_rejects_database_and_manifest_collisions(self):
        connection = sqlite3.connect(self.database)
        connection.execute(
            "INSERT INTO core_pdffile(id, title, file) VALUES (?, ?, ?)",
            (3, "Collision", "pdfs/Private-Name.pdf"),
        )
        connection.commit()
        connection.close()
        with self.assertRaisesMessage(
            CustodyAuditError,
            "document_path_case_collision",
        ):
            audit_missing_pdf_custody(
                database=self.database,
                media_root=self.media,
                hmac_key=self.key,
            )

        connection = sqlite3.connect(self.database)
        connection.execute("DELETE FROM core_pdffile WHERE id=3")
        connection.commit()
        connection.close()
        verified = self._verified()
        verified.manifest["files"].append(
            {
                **verified.manifest["files"][0],
                "path": "media/pdfs/Private-Name.pdf",
            }
        )

        def verify(*args, **kwargs):
            del args, kwargs
            return verified

        with self.assertRaisesMessage(
            CustodyAuditError,
            "manifest_path_case_collision",
        ):
            audit_missing_pdf_custody(
                database=self.database,
                media_root=self.media,
                hmac_key=self.key,
                vault=FakeVault(),
                profile=self.profile,
                list_generation_ids=lambda *args, **kwargs: (
                    ["generation"],
                    True,
                ),
                verify_generation=verify,
            )

    def test_generation_and_evidence_limits_report_truncation(self):
        def list_ids(*args, **kwargs):
            del args, kwargs
            return ["one"], False

        result = audit_missing_pdf_custody(
            database=self.database,
            media_root=self.media,
            hmac_key=self.key,
            vault=FakeVault(),
            profile=self.profile,
            list_generation_ids=list_ids,
            verify_generation=lambda *args, **kwargs: self._verified(),
            max_generations=1,
            max_total_evidence=1,
            max_evidence_per_reference=1,
        )
        self.assertFalse(result["complete"])
        self.assertEqual(result["vault_posture"], "partial")
        self.assertTrue(result["truncation"]["generation_limit"])
        self.assertEqual(
            result["vault_generation_counts"],
            {
                "returned": 1,
                "scanned": 1,
                "verified": 1,
                "listing_complete": False,
            },
        )

    def test_listing_failure_never_claims_no_match_complete(self):
        result = audit_missing_pdf_custody(
            database=self.database,
            media_root=self.media,
            hmac_key=self.key,
            vault=FakeVault(),
            profile=self.profile,
            list_generation_ids=lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("private paginator cursor")
            ),
            verify_generation=lambda *args, **kwargs: self._verified(),
        )
        self.assertEqual(result["vault_posture"], "unavailable")
        self.assertFalse(result["complete"])
        self.assertNotIn("private paginator cursor", json.dumps(result))

    def test_component_symlink_is_rejected(self):
        alias = self.root.parent / f"{self.root.name}-alias"
        alias.symlink_to(self.root, target_is_directory=True)
        self.addCleanup(alias.unlink)
        with self.assertRaisesMessage(
            CustodyAuditError,
            "application_custody_path_unavailable",
        ):
            audit_missing_pdf_custody(
                database=alias / "db.sqlite3",
                media_root=self.media,
                hmac_key=self.key,
            )

        media_alias = self.root / "media-alias"
        media_alias.symlink_to(self.media, target_is_directory=True)
        with self.assertRaisesMessage(
            CustodyAuditError,
            "application_custody_path_unavailable",
        ):
            audit_missing_pdf_custody(
                database=self.database,
                media_root=media_alias,
                hmac_key=self.key,
            )

    def test_archive_identity_change_is_reported_without_evidence_claim(self):
        archive = self._archive()
        from core import custody_audit

        real_open = custody_audit._open_path_no_follow

        def changed_archive_open(path, **kwargs):
            descriptor, metadata = real_open(path, **kwargs)
            if path == archive:
                metadata = self._changed_metadata(metadata)
            return descriptor, metadata

        with patch(
            "core.custody_audit._open_path_no_follow",
            side_effect=changed_archive_open,
        ):
            result = audit_missing_pdf_custody(
                database=self.database,
                media_root=self.media,
                hmac_key=self.key,
                archives=[archive],
            )
        self.assertEqual(
            result["archive_progress"][0]["posture"],
            "archive_changed_during_audit",
        )
        self.assertFalse(result["complete"])

    def test_database_identity_change_fails_before_output(self):
        from core import custody_audit

        real_open = custody_audit._open_entry_no_follow

        def changed_database_open(directory_fd, name, flags, **kwargs):
            descriptor, metadata = real_open(
                directory_fd, name, flags, **kwargs
            )
            if name == self.database.name:
                metadata = self._changed_metadata(metadata)
            return descriptor, metadata

        with patch(
            "core.custody_audit._open_entry_no_follow",
            side_effect=changed_database_open,
        ):
            with self.assertRaisesMessage(
                CustodyAuditError,
                "application_database_changed",
            ):
                audit_missing_pdf_custody(
                    database=self.database,
                    media_root=self.media,
                    hmac_key=self.key,
                )

    def test_hmac_key_requires_single_link_and_stable_metadata(self):
        key_path = self.root / "linked.key"
        key_path.write_bytes(self.key)
        os.chmod(key_path, 0o600)
        second_link = self.root / "linked-again.key"
        os.link(key_path, second_link)
        with self.assertRaisesMessage(
            Exception,
            "hmac_key_file_unsafe",
        ):
            call_command(
                "audit_missing_pdf_custody",
                hmac_key_file=str(key_path),
                database=str(self.database),
                media_root=str(self.media),
                skip_vault=True,
            )

    def test_hmac_key_requires_current_uid_and_stable_fstat(self):
        key_path = self.root / "owned.key"
        key_path.write_bytes(self.key)
        os.chmod(key_path, 0o600)
        with patch(
            "core.custody_audit.os.geteuid",
            return_value=os.geteuid() + 1,
        ):
            with self.assertRaisesMessage(
                CustodyAuditError,
                "hmac_key_file_unsafe",
            ):
                read_hmac_key_file(key_path)

        from core import custody_audit

        real_open = custody_audit._open_path_no_follow

        def changed_key_open(path, **kwargs):
            descriptor, metadata = real_open(path, **kwargs)
            if path == key_path:
                metadata = self._changed_metadata(metadata)
            return descriptor, metadata

        with patch(
            "core.custody_audit._open_path_no_follow",
            side_effect=changed_key_open,
        ):
            with self.assertRaisesMessage(
                CustodyAuditError,
                "hmac_key_file_changed",
            ):
                read_hmac_key_file(key_path)

    def test_missing_reference_limit_fails_closed(self):
        connection = sqlite3.connect(self.database)
        connection.execute(
            "INSERT INTO core_pdffile(id, title, file) VALUES (?, ?, ?)",
            (3, "Another missing", "pdfs/another.pdf"),
        )
        connection.commit()
        connection.close()
        with patch("core.custody_audit.MAX_MISSING_REFERENCES", 1):
            with self.assertRaisesMessage(
                CustodyAuditError,
                "missing_reference_limit_exceeded",
            ):
                audit_missing_pdf_custody(
                    database=self.database,
                    media_root=self.media,
                    hmac_key=self.key,
                )

    def test_archive_deadline_is_bounded_and_reported(self):
        with patch(
            "core.custody_audit._BoundedArchiveReader.read",
            side_effect=CustodyAuditError("archive_time_limit_exceeded"),
        ):
            result = audit_missing_pdf_custody(
                database=self.database,
                media_root=self.media,
                hmac_key=self.key,
                archives=[self._archive()],
                max_seconds=1,
            )
        self.assertEqual(
            result["archive_progress"][0]["posture"],
            "archive_time_limit_exceeded",
        )
        self.assertTrue(result["truncation"]["time_limit"])

    def test_archive_case_collision_is_rejected(self):
        archive = self.root / "case-collision.tar"
        with tarfile.open(archive, "w") as bundle:
            for name in (
                "volume/media/pdfs/private-name.pdf",
                "volume/media/pdfs/Private-Name.pdf",
            ):
                info = tarfile.TarInfo(name)
                info.size = len(self.payload)
                bundle.addfile(info, io.BytesIO(self.payload))
        self._canonicalize_archive(archive)
        result = audit_missing_pdf_custody(
            database=self.database,
            media_root=self.media,
            hmac_key=self.key,
            archives=[archive],
        )
        self.assertEqual(
            result["archive_progress"][0]["posture"],
            "archive_path_case_collision",
        )
        self.assertEqual(
            result["results"][0]["evidence"],
            [{"evidence_class": "no_match"}],
        )

    def test_short_hmac_key_and_unsafe_archive_fail_with_stable_codes(self):
        with self.assertRaisesMessage(CustodyAuditError, "hmac_key_too_short"):
            audit_missing_pdf_custody(
                database=self.database,
                media_root=self.media,
                hmac_key=b"short",
            )
        result = audit_missing_pdf_custody(
            database=self.database,
            media_root=self.media,
            hmac_key=self.key,
            archives=[self.root / "missing-private-name.tar"],
        )
        self.assertEqual(result["archive_posture"], "partial")
        self.assertEqual(result["archive_progress"][0]["posture"], "unavailable")

    def test_command_archive_only_emits_redacted_json(self):
        key_path = self.root / "audit.key"
        key_path.write_bytes(self.key)
        os.chmod(key_path, 0o600)
        output = io.StringIO()
        call_command(
            "audit_missing_pdf_custody",
            hmac_key_file=str(key_path),
            database=str(self.database),
            media_root=str(self.media),
            skip_vault=True,
            archive=[str(self._archive())],
            stdout=output,
        )
        result = json.loads(output.getvalue())
        self.assertTrue(result["read_only"])
        self.assertEqual(result["vault_posture"], "not_requested")
        self.assertNotIn("private-name.pdf", output.getvalue())

    def test_command_rejects_group_readable_hmac_key(self):
        key_path = self.root / "audit.key"
        key_path.write_bytes(self.key)
        os.chmod(key_path, 0o640)
        with self.assertRaisesMessage(Exception, "hmac_key_file_unsafe"):
            call_command(
                "audit_missing_pdf_custody",
                hmac_key_file=str(key_path),
                database=str(self.database),
                media_root=str(self.media),
                skip_vault=True,
            )

    def test_database_scan_is_bound_to_opened_inode_during_swap_and_restore(self):
        replacement = self.root / "replacement.sqlite3"
        connection = sqlite3.connect(replacement)
        connection.execute(
            "CREATE TABLE core_pdffile (id INTEGER PRIMARY KEY, file TEXT)"
        )
        connection.execute(
            "INSERT INTO core_pdffile(id, file) VALUES (99, 'pdfs/replacement.pdf')"
        )
        connection.commit()
        connection.close()
        held = self.root / "held.sqlite3"
        real_read = os.read
        swapped = False

        def swap_then_read(descriptor, size):
            nonlocal swapped
            if not swapped:
                swapped = True
                self.database.rename(held)
                replacement.rename(self.database)
                self.database.rename(replacement)
                held.rename(self.database)
            return real_read(descriptor, size)

        with patch("core.custody_audit.os.read", side_effect=swap_then_read):
            with self.assertRaisesMessage(
                CustodyAuditError, "application_database_changed"
            ):
                audit_missing_pdf_custody(
                    database=self.database,
                    media_root=self.media,
                    hmac_key=self.key,
                )

    def test_database_wal_is_deliberately_rejected(self):
        Path(f"{self.database}-wal").write_bytes(b"")
        with self.assertRaisesMessage(
            CustodyAuditError, "application_database_wal_unsupported"
        ):
            audit_missing_pdf_custody(
                database=self.database,
                media_root=self.media,
                hmac_key=self.key,
            )

    def test_noncanonical_database_paths_fail_closed(self):
        for value in ("", None, "pdfs/./bad.pdf", "pdfs//bad.pdf", "pdfs/bad.pdf/"):
            connection = sqlite3.connect(self.database)
            connection.execute(
                "UPDATE core_pdffile SET file=?, file_path=NULL WHERE id=1",
                (value,),
            )
            connection.commit()
            connection.close()
            with self.assertRaisesMessage(CustodyAuditError, "document_path_invalid"):
                audit_missing_pdf_custody(
                    database=self.database,
                    media_root=self.media,
                    hmac_key=self.key,
                )

    def test_noncanonical_manifest_and_tar_paths_are_rejected(self):
        for path in (
            "media/./pdfs/private-name.pdf",
            "media//pdfs/private-name.pdf",
            "media/pdfs/private-name.pdf/",
        ):
            verified = self._verified()
            verified.manifest["files"][0]["path"] = path
            with self.assertRaisesMessage(CustodyAuditError, "manifest_path_invalid"):
                audit_missing_pdf_custody(
                    database=self.database,
                    media_root=self.media,
                    hmac_key=self.key,
                    vault=FakeVault(),
                    profile=self.profile,
                    list_generation_ids=lambda *args, **kwargs: (["one"], True),
                    verify_generation=lambda *args, **kwargs: verified,
                )
        for index, path in enumerate(
            (
                "./volume/media/pdfs/private-name.pdf",
                "volume//media/pdfs/private-name.pdf",
                "volume/media/pdfs/private-name.pdf/",
            )
        ):
            archive = self.root / f"noncanonical-{index}.tar"
            with tarfile.open(archive, "w") as bundle:
                info = tarfile.TarInfo(path)
                info.size = len(self.payload)
                bundle.addfile(info, io.BytesIO(self.payload))
            self._canonicalize_archive(archive)
            result = audit_missing_pdf_custody(
                database=self.database,
                media_root=self.media,
                hmac_key=self.key,
                archives=[archive],
            )
            self.assertEqual(
                result["archive_progress"][0]["posture"],
                "archive_member_path_invalid",
            )

    def test_tar_requires_two_zero_blocks_and_eof(self):
        valid = self._archive()
        raw = gzip.decompress(valid.read_bytes())
        variants = {
            "one-zero.tar": raw[:-512],
            "bad-second-zero.tar": raw[:-1] + b"x",
            "trailing.tar": raw + b"x",
        }
        for name, content in variants.items():
            archive = self.root / name
            archive.write_bytes(content)
            result = audit_missing_pdf_custody(
                database=self.database,
                media_root=self.media,
                hmac_key=self.key,
                archives=[archive],
            )
            self.assertIn(
                result["archive_progress"][0]["posture"],
                {
                    "archive_member_truncated",
                    "archive_end_marker_invalid",
                    "archive_trailing_data_rejected",
                },
            )

    def test_standard_tarfile_zero_padding_is_accepted(self):
        for compressed in (False, True):
            archive = self.root / (
                "python-standard.tar.gz" if compressed else "python-standard.tar"
            )
            with tarfile.open(archive, "w:gz" if compressed else "w") as bundle:
                info = tarfile.TarInfo("volume/media/pdfs/private-name.pdf")
                info.size = len(self.payload)
                bundle.addfile(info, io.BytesIO(self.payload))
            result = audit_missing_pdf_custody(
                database=self.database,
                media_root=self.media,
                hmac_key=self.key,
                archives=[archive],
            )
            self.assertEqual(result["archive_posture"], "completed")
            self.assertEqual(
                result["results"][0]["evidence"][0]["evidence_class"],
                "path_only_candidate",
            )

    def test_gzip_crc_truncation_and_trailing_members_are_rejected(self):
        valid = bytearray(self._archive().read_bytes())
        corrupt = self.root / "corrupt.tar.gz"
        valid[-8] ^= 0x01
        corrupt.write_bytes(valid)
        truncated = self.root / "truncated.tar.gz"
        truncated.write_bytes(self._archive().read_bytes()[:-4])
        trailing = self.root / "concatenated.tar.gz"
        trailing.write_bytes(self._archive().read_bytes() + gzip.compress(b""))
        expected = {
            corrupt: "archive_gzip_invalid",
            truncated: "archive_gzip_truncated",
            trailing: "archive_trailing_data_rejected",
        }
        for archive, posture in expected.items():
            result = audit_missing_pdf_custody(
                database=self.database,
                media_root=self.media,
                hmac_key=self.key,
                archives=[archive],
            )
            self.assertEqual(result["archive_progress"][0]["posture"], posture)

    def test_database_manifest_and_media_work_limits_fail_closed(self):
        with self.assertRaisesMessage(
            CustodyAuditError, "application_database_row_limit_exceeded"
        ):
            audit_missing_pdf_custody(
                database=self.database,
                media_root=self.media,
                hmac_key=self.key,
                max_database_rows=1,
            )
        with self.assertRaisesMessage(CustodyAuditError, "media_probe_limit_exceeded"):
            audit_missing_pdf_custody(
                database=self.database,
                media_root=self.media,
                hmac_key=self.key,
                max_media_probes=1,
            )
        verified = self._verified()
        verified.manifest["files"].append(
            {**verified.manifest["files"][0], "path": "media/pdfs/other.pdf"}
        )
        with self.assertRaisesMessage(CustodyAuditError, "manifest_entry_limit_exceeded"):
            audit_missing_pdf_custody(
                database=self.database,
                media_root=self.media,
                hmac_key=self.key,
                vault=FakeVault(),
                profile=self.profile,
                list_generation_ids=lambda *args, **kwargs: (["one"], True),
                verify_generation=lambda *args, **kwargs: verified,
                max_manifest_entries=1,
            )

    def test_deadline_begins_before_database_snapshot(self):
        with patch("core.custody_audit.time.monotonic", side_effect=[0.0, 2.0]):
            with self.assertRaisesMessage(CustodyAuditError, "audit_time_limit_exceeded"):
                audit_missing_pdf_custody(
                    database=self.database,
                    media_root=self.media,
                    hmac_key=self.key,
                    max_seconds=1,
                )

    def test_case_mismatched_directory_entry_is_rejected(self):
        descriptor = os.open(self.root, os.O_RDONLY)
        try:
            with self.assertRaisesMessage(CustodyAuditError, "path_case_mismatch"):
                _require_exact_entry(descriptor, "DB.SQLITE3")
        finally:
            os.close(descriptor)

    def test_directory_enumeration_is_bounded_and_deadline_aware(self):
        descriptor = os.open(self.root, os.O_RDONLY)
        try:
            with patch("core.custody_audit.MAX_DIRECTORY_ENTRIES", 1):
                with self.assertRaisesMessage(
                    CustodyAuditError, "directory_entry_limit_exceeded"
                ):
                    _require_exact_entry(descriptor, "not-present")
            with patch("core.custody_audit.time.monotonic", return_value=2):
                with self.assertRaisesMessage(
                    CustodyAuditError, "audit_time_limit_exceeded"
                ):
                    _require_exact_entry(
                        descriptor,
                        "not-present",
                        deadline=1,
                    )
        finally:
            os.close(descriptor)

    def test_entry_swap_between_listing_and_open_fails_closed(self):
        descriptor = os.open(self.root, os.O_RDONLY)
        before = os.stat("db.sqlite3", dir_fd=descriptor, follow_symlinks=False)
        values = {
            name: getattr(before, name)
            for name in (
                "st_dev",
                "st_ino",
                "st_mode",
                "st_uid",
                "st_nlink",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
            )
        }
        values["st_ino"] += 1
        changed = SimpleNamespace(**values)
        try:
            with patch(
                "core.custody_audit.os.stat",
                side_effect=[before, changed],
            ):
                with self.assertRaisesMessage(
                    CustodyAuditError, "path_changed_during_open"
                ):
                    _open_entry_no_follow(
                        descriptor,
                        "db.sqlite3",
                        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    )
        finally:
            os.close(descriptor)

    def test_manifest_and_head_work_observe_cooperative_deadline(self):
        reference = {
            "row_id": 1,
            "path_token": "token",
            "_path": "pdfs/private-name.pdf",
        }
        manifest = self._verified().manifest
        manifest["files"].insert(
            0,
            {**manifest["files"][0], "path": "media/pdfs/unmatched.pdf"},
        )
        with patch(
            "core.custody_audit.time.monotonic",
            side_effect=[0, 0, 0, 0, 0, 2],
        ):
            result = _scan_vault(
                vault=FakeVault(),
                profile=self.profile,
                references=[reference],
                key=self.key,
                list_generation_ids=lambda *args, **kwargs: (["one"], True),
                verify_generation=lambda *args, **kwargs: SimpleNamespace(
                    manifest=manifest
                ),
                max_pages=1,
                max_generations=1,
                max_evidence_per_reference=1,
                max_total_evidence=1,
                max_manifest_entries=2,
                deadline=1,
            )
        self.assertTrue(result[4]["time_limit"])

        vault = FakeVault()
        with patch(
            "core.custody_audit.time.monotonic",
            side_effect=[0, 0, 0, 0, 0, 0, 2],
        ):
            result = _scan_vault(
                vault=vault,
                profile=self.profile,
                references=[reference],
                key=self.key,
                list_generation_ids=lambda *args, **kwargs: (["one"], True),
                verify_generation=lambda *args, **kwargs: self._verified(),
                max_pages=1,
                max_generations=1,
                max_evidence_per_reference=1,
                max_total_evidence=1,
                max_manifest_entries=1,
                deadline=1,
            )
        self.assertEqual(len(vault.head_calls), 1)
        self.assertTrue(result[4]["time_limit"])

    def test_duplicate_archive_identity_is_rejected(self):
        archive = self._archive()
        alias = self.root / "same-archive.tar.gz"
        os.link(archive, alias)
        result = audit_missing_pdf_custody(
            database=self.database,
            media_root=self.media,
            hmac_key=self.key,
            archives=[archive, alias],
        )
        self.assertEqual(
            result["archive_progress"][1]["posture"], "archive_duplicate_identity"
        )

    @patch("vaultops.services.inventory.verify_generation")
    @patch("vaultops.services.inventory.list_generation_ids")
    @patch("vaultops.services.profiles.vault_for_profile")
    @patch("vaultops.models.VaultConnectionProfile.objects")
    def test_command_reads_existing_profile_without_projection_write(
        self,
        profile_objects,
        vault_for_profile,
        list_ids,
        verify,
    ):
        key_path = self.root / "audit.key"
        key_path.write_bytes(self.key)
        os.chmod(key_path, 0o600)
        profile = Mock()
        profile_objects.using.return_value.filter.return_value.first.return_value = (
            profile
        )
        vault_for_profile.return_value = FakeVault()
        list_ids.return_value = ([], True)
        output = io.StringIO()

        call_command(
            "audit_missing_pdf_custody",
            hmac_key_file=str(key_path),
            database=str(self.database),
            media_root=str(self.media),
            stdout=output,
        )

        profile_objects.using.assert_called_once_with("control")
        profile_objects.using.return_value.filter.assert_called_once()
        vault_for_profile.assert_called_once_with(profile)
        list_ids.assert_called_once()
        verify.assert_not_called()
        self.assertNotIn("private-name.pdf", output.getvalue())
