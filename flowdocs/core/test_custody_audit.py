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

from core.custody_audit import CustodyAuditError, audit_missing_pdf_custody


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
        self.root = Path(self.temporary.name)
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

    def _list_ids(self, vault, profile, *, max_pages):
        self.assertIsInstance(vault, FakeVault)
        self.assertEqual(profile, self.profile)
        self.assertEqual(max_pages, 7)
        return ["generation-with-sensitive-operator-label"]

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
            {"listed": 1, "verified": 1},
        )
        classes = {
            item["evidence_class"]
            for item in result["results"][0]["evidence"]
        }
        self.assertEqual(
            classes,
            {"exact_manifest_object", "exact_manifest_archive"},
        )
        self.assertEqual(
            result["evidence_counts"]["exact_manifest_object"],
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

        def list_ids(vault_arg, profile_arg, *, max_pages):
            del vault_arg, profile_arg, max_pages
            return ["verified-generation", "private-failed-generation"]

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
            {"listed": 2, "verified": 1},
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
        with self.assertRaisesMessage(
            CustodyAuditError,
            "archive_logical_byte_limit_exceeded",
        ):
            audit_missing_pdf_custody(
                database=self.database,
                media_root=self.media,
                hmac_key=self.key,
                archives=[self._archive()],
                max_archive_logical_bytes=5,
            )

    def test_short_hmac_key_and_unsafe_archive_fail_with_stable_codes(self):
        with self.assertRaisesMessage(CustodyAuditError, "hmac_key_too_short"):
            audit_missing_pdf_custody(
                database=self.database,
                media_root=self.media,
                hmac_key=b"short",
            )
        with self.assertRaisesMessage(CustodyAuditError, "archive_unavailable"):
            audit_missing_pdf_custody(
                database=self.database,
                media_root=self.media,
                hmac_key=self.key,
                archives=[self.root / "missing-private-name.tar"],
            )

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
        list_ids.return_value = []
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
