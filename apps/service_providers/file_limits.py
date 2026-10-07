import pathlib
from collections.abc import Callable
from typing import NamedTuple

from apps.files.content_type import detect_content_type

MB = 1024 * 1024


class SendabilityResult(NamedTuple):
    supported: bool
    reason: str


def _normalize(content_type: str) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


def _unknown_type_or_size(content_type: str, content_size: int) -> bool:
    return not content_type or not content_size or content_size <= 0


def _check_size(content_size: int, limit: int, limit_name: str) -> SendabilityResult:
    """Returns a supported result if `content_size` is within `limit`, else one naming the exceeded limit."""
    if content_size <= limit:
        return SendabilityResult(True, "")
    return SendabilityResult(False, f"Exceeds {limit // MB}MB {limit_name}")


def can_send_on_whatsapp(content_type: str, content_size: int) -> SendabilityResult:
    """Meta Cloud API limits: 5MB images, 16MB audio/video, 100MB documents (application/*)."""
    content_type = _normalize(content_type)
    if _unknown_type_or_size(content_type=content_type, content_size=content_size):
        return SendabilityResult(False, "File type or size unknown")

    if content_type.startswith("image/"):
        return _check_size(content_size=content_size, limit=5 * MB, limit_name="image limit for WhatsApp")

    if content_type.startswith(("video/", "audio/")):
        media_type = "video" if content_type.startswith("video/") else "audio"
        return _check_size(content_size=content_size, limit=16 * MB, limit_name=f"{media_type} limit for WhatsApp")

    if content_type.startswith("application/"):
        return _check_size(content_size=content_size, limit=100 * MB, limit_name="document limit for WhatsApp")

    return SendabilityResult(False, f"Unsupported file type '{content_type}' for WhatsApp")


def can_send_on_telegram(content_type: str, content_size: int) -> SendabilityResult:
    """Telegram limits: 10MB images, 50MB audio/video/documents."""
    content_type = _normalize(content_type)
    if _unknown_type_or_size(content_type=content_type, content_size=content_size):
        return SendabilityResult(False, "File type or size unknown")

    if content_type.startswith("image/"):
        return _check_size(content_size=content_size, limit=10 * MB, limit_name="image limit for Telegram")

    if content_type.startswith(("video/", "audio/", "application/")):
        media_type = content_type.split("/")[0]
        if media_type == "application":
            media_type = "document"
        return _check_size(content_size=content_size, limit=50 * MB, limit_name=f"{media_type} limit for Telegram")

    return SendabilityResult(False, f"Unsupported file type '{content_type}' for Telegram")


def can_send_on_slack(content_type: str, content_size: int) -> SendabilityResult:
    """Slack limit: 50MB for all supported types (image/*, video/*, audio/*, application/*)."""
    content_type = _normalize(content_type)
    if _unknown_type_or_size(content_type=content_type, content_size=content_size):
        return SendabilityResult(False, "File type or size unknown")

    if content_type.startswith(("image/", "video/", "audio/", "application/")):
        return _check_size(content_size=content_size, limit=50 * MB, limit_name="file size limit for Slack")

    return SendabilityResult(False, f"Unsupported file type '{content_type}' for Slack")


FILE_SENDABILITY_CHECKERS: dict[str, Callable[[str, int], SendabilityResult]] = {
    "whatsapp": can_send_on_whatsapp,
    "telegram": can_send_on_telegram,
    "slack": can_send_on_slack,
}

EMAIL_MAX_ATTACHMENT_BYTES = 20 * MB

BLOCKED_EXTENSIONS: frozenset[str] = frozenset(
    {
        "exe",
        "bat",
        "cmd",
        "com",
        "scr",
        "ps1",
        "sh",
        "vbs",
        "vbe",
        "wsf",
        "msi",
        "app",
        "dmg",
        "jar",
        "appimage",
        "deb",
        "rpm",
        "iso",
        "img",
    }
)

BLOCKED_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "application/x-msdownload",
        "application/x-msdos-program",
        "application/x-dosexec",
        "application/vnd.microsoft.portable-executable",
        "application/x-bat",
        "application/x-sh",
        "application/x-shellscript",
        "text/x-shellscript",
        "application/x-executable",
        "application/x-mach-binary",
        "application/x-elf",
        "application/x-iso9660-image",
        "application/x-apple-diskimage",
        "application/vnd.debian.binary-package",
        "application/x-rpm",
        "application/x-msi",
        "application/java-archive",
    }
)

# Application-namespaced types that are actually textual. Magic typically
# returns text/plain for these, so a text/* detection should not be flagged
# as a mismatch when the claimed type is one of these. Deliberately excludes
# script types (application/javascript, application/x-sh, ...) — those are
# textual but executable.
TEXT_LIKE_APPLICATION_TYPES: frozenset[str] = frozenset(
    {
        "application/json",
        "application/ld+json",
        "application/manifest+json",
        "application/xml",
        "application/atom+xml",
        "application/rss+xml",
        "application/yaml",
        "application/x-yaml",
        "application/toml",
        "application/x-toml",
        "application/x-ndjson",
    }
)


def _category(content_type: str) -> str:
    """Top-level category for mismatch comparison.
    Maps known textual application/* types (JSON, XML, YAML, ...) to 'text'
    since magic typically returns text/plain for them.
    """
    if content_type in TEXT_LIKE_APPLICATION_TYPES:
        return "text"
    return content_type.split("/", 1)[0]


def is_blocked(extension: str, claimed_type: str, detected_type: str) -> str | None:
    """Returns a rejection reason if the extension or either content type is on the blocklist, else None."""
    claimed_type, detected_type = _normalize(claimed_type), _normalize(detected_type)
    if extension.lower() in BLOCKED_EXTENSIONS:
        return f"file extension '.{extension.lower()}' not allowed"
    if detected_type in BLOCKED_CONTENT_TYPES:
        return f"file type not allowed (detected: {detected_type})"
    if claimed_type in BLOCKED_CONTENT_TYPES:
        return f"file type not allowed (claimed: {claimed_type})"
    return None


def blocked_file_reason(filename: str, claimed_type: str, content: bytes) -> str | None:
    """Returns a rejection reason if an inbound file's extension, claimed type or sniffed type is blocked, else None.

    `content` only needs to hold the start of the file.
    """
    return is_blocked(
        extension=pathlib.Path(filename or "").suffix.lstrip("."),
        claimed_type=claimed_type,
        detected_type=detect_content_type(content, fallback=claimed_type),
    )


def content_type_mismatch(claimed_type: str, detected_type: str) -> str | None:
    """Returns a rejection reason if the claimed and detected types are in different categories, else None."""
    claimed_type, detected_type = _normalize(claimed_type), _normalize(detected_type)
    if not claimed_type or "application/octet-stream" in (claimed_type, detected_type):
        return None
    if _category(claimed_type) == _category(detected_type):
        return None
    return f"content type mismatch (claimed: {claimed_type}, detected: {detected_type})"


def can_send_on_email(content_type: str, content_size: int) -> SendabilityResult:
    """Email: 20 MB cap, executable/installer denylist applies."""
    content_type = _normalize(content_type)
    if _unknown_type_or_size(content_type=content_type, content_size=content_size):
        return SendabilityResult(False, "File type or size unknown")
    if content_type in BLOCKED_CONTENT_TYPES:
        return SendabilityResult(False, f"File type '{content_type}' not allowed for email")
    if content_size > EMAIL_MAX_ATTACHMENT_BYTES:
        return SendabilityResult(False, "Exceeds 20MB email attachment limit")
    return SendabilityResult(True, "")


FILE_SENDABILITY_CHECKERS["email"] = can_send_on_email
