from io import BytesIO
from unittest.mock import patch

import pytest

from apps.files.content_type import (
    DEFAULT_CONTENT_TYPE,
    detect_content_type,
    detect_content_type_from_file,
)


@pytest.fixture()
def magic_returns():
    """Patch python-magic to return a controlled MIME type for deterministic tests."""

    def _patch(value):
        return patch("apps.files.content_type.magic.from_buffer", return_value=value)

    return _patch


class TestDetectContentType:
    def test_uses_magic_when_signature_recognised(self, magic_returns):
        with magic_returns("image/png"):
            assert detect_content_type(b"any-bytes") == "image/png"

    def test_treats_octet_stream_as_no_signal(self, magic_returns):
        with magic_returns(DEFAULT_CONTENT_TYPE):
            assert detect_content_type(b"any-bytes", filename="notes.txt") == "text/plain"

    def test_falls_back_to_filename_when_no_bytes(self):
        assert detect_content_type(b"", filename="notes.txt") == "text/plain"

    def test_falls_back_to_explicit_fallback_when_no_other_signal(self):
        assert detect_content_type(b"", fallback="application/pdf") == "application/pdf"

    def test_returns_default_when_no_signal_at_all(self):
        assert detect_content_type(b"") == DEFAULT_CONTENT_TYPE

    def test_magic_takes_priority_over_filename(self, magic_returns):
        with magic_returns("image/png"):
            assert detect_content_type(b"any-bytes", filename="lie.txt") == "image/png"

    def test_magic_takes_priority_over_fallback(self, magic_returns):
        with magic_returns("image/png"):
            assert detect_content_type(b"any-bytes", fallback="application/pdf") == "image/png"

    def test_filename_takes_priority_over_fallback(self):
        assert detect_content_type(b"", filename="a.json", fallback="application/pdf") == "application/json"

    def test_magic_exception_falls_through(self):
        with patch("apps.files.content_type.magic.from_buffer", side_effect=RuntimeError("libmagic boom")):
            assert detect_content_type(b"any-bytes", fallback="application/pdf") == "application/pdf"


class TestDetectContentTypeFromFile:
    def test_detects_from_file_bytes(self, magic_returns):
        f = BytesIO(b"any-bytes")
        f.name = "image.png"
        with magic_returns("image/png"):
            assert detect_content_type_from_file(f) == "image/png"
        # File must be seeked back to start so callers can re-read it.
        assert f.tell() == 0

    def test_falls_back_to_filename_when_bytes_unknown(self):
        f = BytesIO(b"\x00\x00")
        f.name = "notes.txt"
        assert detect_content_type_from_file(f) == "text/plain"

    def test_returns_default_when_everything_fails(self):
        f = BytesIO(b"\x00\x00")
        f.name = "no_extension"
        assert detect_content_type_from_file(f) == DEFAULT_CONTENT_TYPE

    def test_handles_unreadable_file(self):
        class Broken:
            name = "broken.txt"

            def seek(self, *_):
                raise OSError("cannot seek")

            def read(self, *_):
                raise OSError("cannot read")

        assert detect_content_type_from_file(Broken()) == "text/plain"
