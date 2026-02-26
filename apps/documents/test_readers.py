from io import BytesIO
from unittest import mock

import pytest

from apps.documents.readers import FileReadException, plaintext_reader


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
