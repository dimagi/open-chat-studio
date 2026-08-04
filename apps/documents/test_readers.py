import threading
from io import BytesIO
from pathlib import Path
from unittest import mock

import pytest
from markitdown._exceptions import FileConversionException  # noqa: TID253

from apps.documents.readers import (
    FileReadException,
    default_read,
    get_file_content_reader,
    markitdown_read,
    pdf_read,
    plaintext_reader,
)

TEST_PDF = Path(__file__).parent / "tests" / "data" / "test.pdf"


class TestPlaintextReader:
    """Tests for plaintext_reader error handling and encoding detection."""

    def test_plaintext_reader_utf8_success(self):
        """Test that UTF-8 encoded content is decoded correctly."""
        content = "Hello, world! 🌍".encode()
        file_obj = BytesIO(content)

        doc = plaintext_reader(file_obj)

        assert len(doc.parts) == 1
        assert doc.parts[0].content == "Hello, world! 🌍"

    @pytest.mark.parametrize(("encoding", "text"), [("windows-1252", "Hello World! Special chars: café résumé")])
    def test_plaintext_reader_encoding_detection_success(self, encoding: str, text: str):
        """Test that non-UTF-8 encoded content is detected and decoded."""
        content = text.encode(encoding)
        assert content != text  # Ensure encoding changed the byte representation
        file_obj = BytesIO(content)

        doc = plaintext_reader(file_obj)

        assert len(doc.parts) == 1
        assert doc.parts[0].content == text

    def test_plaintext_reader_unicode_dammit_returns_none(self):
        """Test that None from UnicodeDammit is handled as FileReadException."""
        content = b"\x80\x81\x82\x83"  # Invalid bytes for most encodings
        file_obj = BytesIO(content)

        with mock.patch("apps.documents.readers.UnicodeDammit") as mock_dammit:
            mock_dammit.return_value.unicode_markup = None

            with pytest.raises(FileReadException, match="Unable to decode file contents to text"):
                plaintext_reader(file_obj)

    def test_plaintext_reader_unicode_dammit_raises_exception(self):
        """Test that exceptions from UnicodeDammit are caught and wrapped."""
        content = b"\x80\x81\x82\x83"
        file_obj = BytesIO(content)

        with mock.patch("apps.documents.readers.UnicodeDammit") as mock_dammit:
            mock_dammit.side_effect = Exception("Encoding detection failed")

            with pytest.raises(FileReadException, match="Unable to decode file contents to text"):
                plaintext_reader(file_obj)


class TestPdfRead:
    def test_reads_pdf_via_pdfium(self):
        doc = pdf_read(BytesIO(TEST_PDF.read_bytes()))

        assert doc.get_contents_as_string() == "PDF documents can be\nhard to read 🫠\n"

    def test_normalises_line_endings(self):
        """PDFium emits \\r\\n; carriage returns would otherwise reach the index verbatim."""
        with mock.patch("pypdfium2.PdfDocument") as pdfium_doc:
            page = mock.Mock()
            page.get_textpage.return_value.get_text_range.return_value = "line one\r\nline two\r"
            pdf = mock.MagicMock()
            pdf.__iter__.return_value = iter([page])
            pdfium_doc.return_value = pdf

            doc = pdf_read(BytesIO(b"%PDF-1.4 stub"))

        pdf.close.assert_called_once()

        assert "\r" not in doc.get_contents_as_string()
        assert doc.get_contents_as_string() == "line one\nline two\n"

    def test_concurrent_reads_do_not_corrupt_pdfium(self):
        """PDFium is not thread-safe and both the Celery workers (--pool threads) and gunicorn
        (--threads 8) parse PDFs concurrently in one process. Without serialisation, concurrent
        parses corrupt library state and valid PDFs fail with 'Data format error'."""
        pdf_bytes = TEST_PDF.read_bytes()
        errors = []

        def parse_repeatedly():
            for _ in range(20):
                try:
                    pdf_read(BytesIO(pdf_bytes))
                except Exception as exc:
                    errors.append(exc)

        threads = [threading.Thread(target=parse_repeatedly) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert not errors

    def test_a_damaged_file_is_refused_rather_than_handed_to_pdfminer(self):
        """pdfminer takes minutes on a damaged file with no way to interrupt it, so a file
        PDFium cannot open fails here instead of falling back to it."""
        with (
            mock.patch("apps.documents.readers.markitdown_read") as markitdown,
            pytest.raises(FileReadException, match="Could not extract text from this PDF"),
        ):
            pdf_read(BytesIO(b"%PDF-1.4 truncated before the xref"))

        markitdown.assert_not_called()


class TestDefaultRead:
    def test_sniffs_pdf_magic_and_uses_the_pdf_reader(self):
        """The sync loader has no trustworthy content type, so the bytes have to say."""
        with mock.patch("apps.documents.readers.pdf_read") as pdf:
            pdf.return_value = mock.sentinel.pdf_document
            result = default_read(BytesIO(TEST_PDF.read_bytes()))

        assert result is mock.sentinel.pdf_document

    def test_a_pdf_with_junk_before_the_header_is_still_read_as_pdf(self):
        """The PDF header may sit anywhere in the first 1024 bytes; mail gateways and bad HTTP
        responses produce such files. They must reach PDFium, not fall through to pdfminer."""
        junked = b"X" * 100 + TEST_PDF.read_bytes()

        doc = default_read(BytesIO(junked))

        # this exact output proves the PDFium path ran; pdfminer renders it differently
        assert doc.get_contents_as_string() == "PDF documents can be\nhard to read 🫠\n"

    def test_pdf_magic_beyond_the_first_kilobyte_is_not_treated_as_pdf(self):
        with mock.patch("apps.documents.readers.markitdown_read") as markitdown:
            markitdown.return_value = mock.sentinel.other_document
            result = default_read(BytesIO(b"X" * 2000 + b"%PDF-1.4"))

        assert result is mock.sentinel.other_document

    def test_non_pdf_goes_to_markitdown(self):
        with mock.patch("apps.documents.readers.markitdown_read") as markitdown:
            markitdown.return_value = mock.sentinel.other_document
            result = default_read(BytesIO(b"PK\x03\x04 a docx, not a pdf"))

        assert result is mock.sentinel.other_document

    def test_unknown_content_type_falls_back_to_sniffing(self):
        assert get_file_content_reader("application/octet-stream") is default_read


class TestBinaryIsNeverDecodedAsText:
    def test_unreadable_pdf_fails_instead_of_being_decoded(self):
        """markitdown falls back to a plaintext decode when no converter can read a file. For a
        PDF that puts ~4MB of the raw file into the index as 'text', which is worse than
        failing: mean word length of that output is 158 characters."""
        with mock.patch("markitdown.MarkItDown") as markitdown:
            markitdown.return_value.convert.side_effect = FileConversionException(attempts=[])

            with pytest.raises(FileReadException, match="Could not extract text"):
                markitdown_read(BytesIO(b"%PDF-1.4 binary junk"))

    def test_unreadable_pdf_with_offset_header_fails_instead_of_being_decoded(self):
        with mock.patch("markitdown.MarkItDown") as markitdown:
            markitdown.return_value.convert.side_effect = FileConversionException(attempts=[])

            with pytest.raises(FileReadException, match="Could not extract text"):
                markitdown_read(BytesIO(b"junk prefix %PDF-1.4 then binary junk"))

    def test_undecodable_non_pdf_still_falls_back_to_plaintext(self):
        """The fallback is useful for genuinely textual files with an unknown extension."""
        with mock.patch("markitdown.MarkItDown") as markitdown:
            markitdown.return_value.convert.side_effect = FileConversionException(attempts=[])

            doc = markitdown_read(BytesIO(b"just some text"))

        assert doc.get_contents_as_string() == "just some text"
