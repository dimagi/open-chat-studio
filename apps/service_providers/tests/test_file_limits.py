import pytest

from apps.service_providers.file_limits import SendabilityResult, can_send_on_whatsapp

MB = 1024 * 1024


class TestCanSendOnWhatsapp:
    """Tests for WhatsApp (Meta Cloud API) file limits."""

    @pytest.mark.parametrize(
        ("content_type", "content_size", "expected_supported"),
        [
            # Images: 5MB limit
            ("image/jpeg", 1 * MB, True),
            ("image/png", 5 * MB, True),  # exactly at limit
            ("image/gif", 5 * MB + 1, False),  # 1 byte over
            ("image/jpeg", 6 * MB, False),
            # Audio: 16MB limit
            ("audio/mpeg", 1 * MB, True),
            ("audio/ogg", 16 * MB, True),  # exactly at limit
            ("audio/wav", 16 * MB + 1, False),  # 1 byte over
            # Video: 16MB limit
            ("video/mp4", 16 * MB, True),  # exactly at limit
            ("video/mp4", 17 * MB, False),
            # Documents: 100MB limit
            ("application/pdf", 50 * MB, True),
            ("application/pdf", 100 * MB, True),  # exactly at limit
            ("application/zip", 100 * MB + 1, False),  # 1 byte over
            # Unsupported MIME types
            ("text/plain", 1024, False),
            ("font/woff2", 1024, False),
        ],
    )
    def test_mime_and_size_limits(self, content_type, content_size, expected_supported):
        result = can_send_on_whatsapp(content_type, content_size)
        assert isinstance(result, SendabilityResult)
        assert result.supported is expected_supported

    def test_unsupported_has_reason(self):
        result = can_send_on_whatsapp("image/jpeg", 6 * MB)
        assert result.supported is False
        assert result.reason  # reason must not be empty

    def test_supported_has_empty_reason(self):
        result = can_send_on_whatsapp("image/jpeg", 1 * MB)
        assert result.supported is True
        assert result.reason == ""

    @pytest.mark.parametrize(
        ("content_type", "content_size"),
        [
            ("", 1024),
            ("image/jpeg", 0),
            ("", 0),
        ],
    )
    def test_missing_data_returns_unsupported(self, content_type, content_size):
        result = can_send_on_whatsapp(content_type, content_size)
        assert result.supported is False
        assert "unknown" in result.reason.lower()
