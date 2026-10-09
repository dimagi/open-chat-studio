import pytest

from apps.service_providers.file_limits import (
    BLOCKED_CONTENT_TYPES,
    BLOCKED_EXTENSIONS,
    EMAIL_MAX_ATTACHMENT_BYTES,
    FILE_SENDABILITY_CHECKERS,
    TEXT_LIKE_APPLICATION_TYPES,
    SendabilityResult,
    blocked_file_reason,
    can_send_on_email,
    can_send_on_slack,
    can_send_on_telegram,
    can_send_on_whatsapp,
    content_type_mismatch,
    is_blocked,
)

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


class TestCanSendOnTelegram:
    """Tests for Telegram Bot API file limits."""

    @pytest.mark.parametrize(
        ("content_type", "content_size", "expected_supported"),
        [
            # Images: 10MB limit
            ("image/jpeg", 1 * MB, True),
            ("image/png", 10 * MB, True),  # exactly at limit
            ("image/gif", 10 * MB + 1, False),  # 1 byte over
            # Audio/Video/Docs: 50MB limit
            ("audio/mpeg", 50 * MB, True),  # exactly at limit
            ("video/mp4", 50 * MB + 1, False),  # 1 byte over
            ("application/pdf", 50 * MB, True),
            # Unsupported MIME types
            ("text/plain", 1024, False),
            ("font/woff2", 1024, False),
        ],
    )
    def test_mime_and_size_limits(self, content_type, content_size, expected_supported):
        result = can_send_on_telegram(content_type, content_size)
        assert isinstance(result, SendabilityResult)
        assert result.supported is expected_supported

    def test_unsupported_has_reason(self):
        result = can_send_on_telegram("image/jpeg", 11 * MB)
        assert result.supported is False
        assert result.reason

    @pytest.mark.parametrize(
        ("content_type", "content_size"),
        [("", 1024), ("image/jpeg", 0)],
    )
    def test_missing_data_returns_unsupported(self, content_type, content_size):
        result = can_send_on_telegram(content_type, content_size)
        assert result.supported is False
        assert "unknown" in result.reason.lower()


class TestCanSendOnSlack:
    """Tests for Slack file limits (50MB for all supported types)."""

    @pytest.mark.parametrize(
        ("content_type", "content_size", "expected_supported"),
        [
            ("image/jpeg", 1 * MB, True),
            ("video/mp4", 50 * MB, True),  # exactly at limit
            ("audio/mpeg", 50 * MB + 1, False),  # 1 byte over
            ("application/pdf", 50 * MB, True),
            ("text/plain", 1024, False),
        ],
    )
    def test_mime_and_size_limits(self, content_type, content_size, expected_supported):
        result = can_send_on_slack(content_type, content_size)
        assert isinstance(result, SendabilityResult)
        assert result.supported is expected_supported

    @pytest.mark.parametrize(
        ("content_type", "content_size"),
        [("", 1024), ("image/jpeg", 0)],
    )
    def test_missing_data_returns_unsupported(self, content_type, content_size):
        result = can_send_on_slack(content_type, content_size)
        assert result.supported is False


class TestCanSendOnEmail:
    """Tests for email file limits (20MB cap, executable denylist)."""

    @pytest.mark.parametrize(
        ("content_type", "content_size", "expected_supported"),
        [
            # Under 20MB limit — allowed types
            ("application/pdf", 1 * MB, True),
            ("image/jpeg", 5 * MB, True),
            ("text/plain", 1 * MB, True),
            # Exactly at limit
            ("application/pdf", EMAIL_MAX_ATTACHMENT_BYTES, True),
            # 1 byte over limit
            ("application/pdf", EMAIL_MAX_ATTACHMENT_BYTES + 1, False),
            # Blocked content types
            ("application/x-msdownload", 1 * MB, False),
            ("application/x-sh", 1 * MB, False),
            ("application/java-archive", 1 * MB, False),
            ("application/x-msi", 1 * MB, False),
        ],
    )
    def test_mime_and_size_limits(self, content_type, content_size, expected_supported):
        result = can_send_on_email(content_type, content_size)
        assert isinstance(result, SendabilityResult)
        assert result.supported is expected_supported

    def test_blocked_type_has_reason(self):
        result = can_send_on_email("application/x-msdownload", 1 * MB)
        assert result.supported is False
        assert result.reason

    def test_over_size_has_reason(self):
        result = can_send_on_email("application/pdf", EMAIL_MAX_ATTACHMENT_BYTES + 1)
        assert result.supported is False
        assert "20MB" in result.reason

    def test_supported_has_empty_reason(self):
        result = can_send_on_email("application/pdf", 1 * MB)
        assert result.supported is True
        assert result.reason == ""

    @pytest.mark.parametrize(
        ("content_type", "content_size"),
        [
            ("", 1024),
            ("application/pdf", 0),
            ("application/pdf", -1),
        ],
    )
    def test_unknown_type_or_size(self, content_type, content_size):
        result = can_send_on_email(content_type, content_size)
        assert result.supported is False
        assert "unknown" in result.reason.lower()

    def test_content_type_params_stripped(self):
        """MIME parameters (e.g. charset) must not cause false rejections."""
        result = can_send_on_email("application/pdf; charset=utf-8", 1 * MB)
        assert result.supported is True

    def test_registered_in_checkers(self):
        assert FILE_SENDABILITY_CHECKERS["email"] is can_send_on_email

    def test_constants_exposed(self):
        assert EMAIL_MAX_ATTACHMENT_BYTES == 20 * MB
        assert "exe" in BLOCKED_EXTENSIONS
        assert "application/x-msdownload" in BLOCKED_CONTENT_TYPES
        assert "application/json" in TEXT_LIKE_APPLICATION_TYPES
        # Script types deliberately excluded from text-like allowlist
        assert "application/javascript" not in TEXT_LIKE_APPLICATION_TYPES
        assert "application/x-sh" not in TEXT_LIKE_APPLICATION_TYPES


class TestChannelChecksRegistry:
    """Tests for the FILE_SENDABILITY_CHECKERS registry."""

    def test_registry_contains_expected_channels(self):
        assert set(FILE_SENDABILITY_CHECKERS.keys()) == {"whatsapp", "telegram", "slack", "email"}

    def test_registry_values_are_callable(self):
        for name, func in FILE_SENDABILITY_CHECKERS.items():
            result = func("image/jpeg", 1 * MB)
            assert isinstance(result, SendabilityResult), f"{name} checker returned wrong type"


class TestIsBlocked:
    @pytest.mark.parametrize(
        ("extension", "claimed", "detected", "expected"),
        [
            pytest.param("exe", "text/plain", "text/plain", "file extension '.exe' not allowed", id="exe"),
            pytest.param("EXE", "text/plain", "text/plain", "file extension '.exe' not allowed", id="extension-case"),
            pytest.param("dmg", "", "application/octet-stream", "file extension '.dmg' not allowed", id="dmg"),
            pytest.param(
                "txt",
                "text/plain",
                "application/x-executable",
                "file type not allowed (detected: application/x-executable)",
                id="blocked-detected-type",
            ),
            pytest.param(
                "txt",
                "Application/X-MSDownload; name=setup.txt",
                "text/plain",
                "file type not allowed (claimed: application/x-msdownload)",
                id="blocked-claimed-type-with-params",
            ),
            pytest.param("pdf", "application/pdf", "application/pdf", None, id="allowed-pdf"),
            pytest.param("png", "image/png", "image/png", None, id="allowed-image"),
            pytest.param("csv", "application/vnd.ms-excel", "text/plain", None, id="mismatch-not-checked"),
        ],
    )
    def test_extension_and_content_type(self, extension, claimed, detected, expected):
        assert is_blocked(extension=extension, claimed_type=claimed, detected_type=detected) == expected


class TestBlockedFileReason:
    ELF_BYTES = b"\x7fELF\x02\x01\x01\x00" + bytes(8) + b"\x02\x00\x3e\x00\x01\x00\x00\x00" + bytes(40)

    @pytest.mark.parametrize(
        ("filename", "claimed", "content", "expected"),
        [
            pytest.param("Setup.EXE", "text/plain", b"MZ", "file extension '.exe' not allowed", id="extension"),
            pytest.param(
                "notes.txt",
                "text/plain",
                ELF_BYTES,
                "file type not allowed (detected: application/x-executable)",
                id="sniffed-type",
            ),
            pytest.param(
                "notes.txt",
                "application/x-msdownload",
                b"",
                "file type not allowed (detected: application/x-msdownload)",
                id="claimed-type-when-sniffing-finds-nothing",
            ),
            pytest.param(
                "report.pdf",
                "application/pdf",
                b"MZ\x90\x00" + bytes(60),
                "file type not allowed (detected: application/x-dosexec)",
                id="windows-executable-renamed-to-pdf",
            ),
            pytest.param(
                "run.sh",
                "text/plain",
                b"echo hi",
                "file extension '.sh' not allowed",
                id="shell-script-extension",
            ),
            pytest.param(
                "notes.txt",
                "text/plain",
                b"#!/bin/sh\necho hi\n",
                "file type not allowed (detected: text/x-shellscript)",
                id="shell-script-sniffed",
            ),
            pytest.param(
                "run.bat.", "text/plain", b"@echo off", "file extension '.bat' not allowed", id="trailing-dot"
            ),
            pytest.param(
                "run.bat ", "text/plain", b"@echo off", "file extension '.bat' not allowed", id="trailing-space"
            ),
            pytest.param("report.pdf", "application/pdf", b"%PDF-1.4 fake", None, id="allowed"),
            pytest.param("", "", b"", None, id="no-name-or-type"),
        ],
    )
    def test_reason(self, filename, claimed, content, expected):
        assert blocked_file_reason(filename=filename, claimed_type=claimed, content=content) == expected


class TestContentTypeMismatch:
    @pytest.mark.parametrize(
        ("claimed", "detected", "should_block"),
        [
            pytest.param("image/jpeg", "application/pdf", True, id="cross-category"),
            pytest.param("application/vnd.ms-excel", "text/plain", True, id="excel-claim-for-text"),
            pytest.param("text/csv", "application/javascript", True, id="script-not-text-like"),
            pytest.param("application/octet-stream", "application/pdf", False, id="claimed-unknown"),
            pytest.param("application/pdf", "application/octet-stream", False, id="detected-unknown"),
            pytest.param("", "application/pdf", False, id="no-claim"),
            pytest.param("application/json", "text/plain", False, id="text-like-json"),
            pytest.param("application/xml", "text/plain", False, id="text-like-xml"),
            pytest.param("text/csv", "text/plain", False, id="same-category"),
            pytest.param("Application/PDF; name=x.pdf", "application/pdf", False, id="normalized"),
        ],
    )
    def test_mismatch(self, claimed, detected, should_block):
        result = content_type_mismatch(claimed_type=claimed, detected_type=detected)
        assert (result is not None) == should_block
