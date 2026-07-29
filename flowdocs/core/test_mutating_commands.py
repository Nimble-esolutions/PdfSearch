import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from core.models import Folder, PDFFile


class LegacyMutatingCommandScopeTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="mutation-command-user",
            password="test-password",
            role="superadmin",
        )
        self.folder = Folder.objects.create(
            name="Mutation command folder",
            created_by=self.user,
        )

    def test_reconcile_and_restore_aliases_use_one_outer_scope(self):
        for command_name in (
            "reconcile_media_pdfs",
            "restore_pdfs",
            "restore_pdfs_simple",
        ):
            with self.subTest(command=command_name):
                PDFFile.objects.all().delete()
                with tempfile.TemporaryDirectory() as media_root:
                    pdfs_dir = Path(media_root) / "pdfs"
                    pdfs_dir.mkdir()
                    (pdfs_dir / "one.pdf").write_bytes(b"%PDF-1.7 one")
                    (pdfs_dir / "two.pdf").write_bytes(b"%PDF-1.7 two")

                    with (
                        override_settings(MEDIA_ROOT=media_root),
                        patch(
                            "core.management.commands.reconcile_media_pdfs."
                            "precompute_pdf_embeddings"
                        ),
                        patch(
                            "core.management.commands.restore_pdfs."
                            "precompute_pdf_embeddings"
                        ),
                        patch(
                            "core.management.commands.reconcile_media_pdfs."
                            "mutation_scope"
                        ) as scope,
                    ):
                        call_command(command_name, folder_id=self.folder.pk)

                scope.assert_called_once_with(
                    category="pdf",
                    relative_path="pdfs/",
                    operation="reconcile_media_pdfs",
                    record_on_change=True,
                )
                self.assertEqual(PDFFile.objects.count(), 2)

    def test_reconcile_noop_does_not_open_scope(self):
        with tempfile.TemporaryDirectory() as media_root:
            pdfs_dir = Path(media_root) / "pdfs"
            pdfs_dir.mkdir()
            path = pdfs_dir / "existing.pdf"
            path.write_bytes(b"%PDF-1.7 existing")
            PDFFile.objects.create(
                title="Existing",
                file="pdfs/existing.pdf",
                folder=self.folder,
                uploaded_by=self.user,
            )

            with (
                override_settings(MEDIA_ROOT=media_root),
                patch(
                    "core.management.commands.reconcile_media_pdfs.mutation_scope"
                ) as scope,
            ):
                call_command("reconcile_media_pdfs", folder_id=self.folder.pk)

        scope.assert_not_called()

    def test_unsupported_keyword_commands_fail_explicitly(self):
        for command_name in ("extract_keywords", "extract_keywords_fast"):
            with self.subTest(command=command_name):
                with self.assertRaisesRegex(
                    CommandError,
                    "no supported extraction backend",
                ):
                    call_command(command_name)
