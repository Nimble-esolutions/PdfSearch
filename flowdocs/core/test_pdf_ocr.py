import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from .utils import extract_text_from_pdf_path


class _FakePixmap:
    def save(self, path):
        Path(path).write_bytes(b"fake-png")


class _FakePage:
    rect = SimpleNamespace(width=72, height=72)

    def __init__(self, text=""):
        self.text = text

    def get_text(self, mode):
        return self.text

    def get_pixmap(self, **kwargs):
        return _FakePixmap()


class _FakeDocument:
    def __init__(self, pages):
        self.pages = pages

    def __iter__(self):
        return iter(self.pages)

    def close(self):
        return None


class PdfOcrFallbackTests(SimpleTestCase):
    def _extract(self, document, **settings):
        with tempfile.NamedTemporaryFile(suffix=".pdf") as source:
            with override_settings(**settings):
                with patch("core.utils.fitz.open", return_value=document):
                    return extract_text_from_pdf_path(source.name)

    def test_native_text_does_not_start_ocr(self):
        document = _FakeDocument([_FakePage("native searchable text")])
        with patch("core.utils.subprocess.run") as run:
            text = self._extract(document, PDF_OCR_FALLBACK_ENABLED=True)

        self.assertEqual(text, "native searchable text")
        run.assert_not_called()

    def test_image_only_page_uses_configured_tesseract_languages(self):
        document = _FakeDocument([_FakePage()])
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="सहकारी संस्था\n", stderr=""
        )
        with patch("core.utils.shutil.which", return_value="/usr/bin/tesseract"):
            with patch("core.utils.subprocess.run", return_value=completed) as run:
                text = self._extract(
                    document,
                    PDF_OCR_FALLBACK_ENABLED=True,
                    PDF_OCR_LANGUAGES="eng+mar",
                )

        self.assertEqual(text, "सहकारी संस्था")
        command = run.call_args.args[0]
        self.assertEqual(command[0], "/usr/bin/tesseract")
        self.assertIn("-l", command)
        self.assertEqual(command[command.index("-l") + 1], "eng+mar")

    def test_ocr_failure_fails_closed_without_using_stderr_as_text(self):
        document = _FakeDocument([_FakePage()])
        completed = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="private engine details"
        )
        with patch("core.utils.shutil.which", return_value="/usr/bin/tesseract"):
            with patch("core.utils.subprocess.run", return_value=completed):
                text = self._extract(document, PDF_OCR_FALLBACK_ENABLED=True)

        self.assertEqual(text, "")

    def test_ocr_timeout_fails_closed(self):
        document = _FakeDocument([_FakePage()])
        timeout = subprocess.TimeoutExpired("tesseract", 1)
        with patch("core.utils.shutil.which", return_value="/usr/bin/tesseract"):
            with patch("core.utils.subprocess.run", side_effect=timeout):
                text = self._extract(document, PDF_OCR_FALLBACK_ENABLED=True)

        self.assertEqual(text, "")

    def test_ocr_page_cap_prevents_partial_index_text(self):
        document = _FakeDocument([_FakePage(), _FakePage()])
        with patch("core.utils.subprocess.run") as run:
            text = self._extract(
                document,
                PDF_OCR_FALLBACK_ENABLED=True,
                PDF_OCR_MAX_PAGES=1,
            )

        self.assertEqual(text, "")
        run.assert_not_called()

    def test_disabled_ocr_preserves_native_only_behavior(self):
        document = _FakeDocument([_FakePage()])
        with patch("core.utils.subprocess.run") as run:
            text = self._extract(document, PDF_OCR_FALLBACK_ENABLED=False)

        self.assertEqual(text, "")
        run.assert_not_called()
