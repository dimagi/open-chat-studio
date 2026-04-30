from collections.abc import Callable
from typing import NamedTuple

MB = 1024 * 1024


class SendabilityResult(NamedTuple):
    supported: bool
    reason: str


def can_send_on_whatsapp(content_type: str, content_size: int) -> SendabilityResult:
    """Meta Cloud API limits: 5MB images, 16MB audio/video, 100MB documents (application/*)."""
    content_type = (content_type or "").split(";", 1)[0].strip().lower()
    if not content_type or not content_size or content_size <= 0:
        return SendabilityResult(False, "File type or size unknown")

    if content_type.startswith("image/"):
        limit = 5 * MB
        if content_size <= limit:
            return SendabilityResult(True, "")
        return SendabilityResult(False, f"Exceeds {limit // MB}MB image limit for WhatsApp")

    if content_type.startswith(("video/", "audio/")):
        limit = 16 * MB
        if content_size <= limit:
            return SendabilityResult(True, "")
        media_type = "video" if content_type.startswith("video/") else "audio"
        return SendabilityResult(False, f"Exceeds {limit // MB}MB {media_type} limit for WhatsApp")

    if content_type.startswith("application/"):
        limit = 100 * MB
        if content_size <= limit:
            return SendabilityResult(True, "")
        return SendabilityResult(False, f"Exceeds {limit // MB}MB document limit for WhatsApp")

    return SendabilityResult(False, f"Unsupported file type '{content_type}' for WhatsApp")


def can_send_on_telegram(content_type: str, content_size: int) -> SendabilityResult:
    """Telegram limits: 10MB images, 50MB audio/video/documents."""
    content_type = (content_type or "").split(";", 1)[0].strip().lower()
    if not content_type or not content_size or content_size <= 0:
        return SendabilityResult(False, "File type or size unknown")

    if content_type.startswith("image/"):
        limit = 10 * MB
        if content_size <= limit:
            return SendabilityResult(True, "")
        return SendabilityResult(False, f"Exceeds {limit // MB}MB image limit for Telegram")

    if content_type.startswith(("video/", "audio/", "application/")):
        limit = 50 * MB
        if content_size <= limit:
            return SendabilityResult(True, "")
        media_type = content_type.split("/")[0]
        if media_type == "application":
            media_type = "document"
        return SendabilityResult(False, f"Exceeds {limit // MB}MB {media_type} limit for Telegram")

    return SendabilityResult(False, f"Unsupported file type '{content_type}' for Telegram")


def can_send_on_slack(content_type: str, content_size: int) -> SendabilityResult:
    """Slack limit: 50MB for all supported types (image/*, video/*, audio/*, application/*)."""
    content_type = (content_type or "").split(";", 1)[0].strip().lower()
    if not content_type or not content_size or content_size <= 0:
        return SendabilityResult(False, "File type or size unknown")

    limit = 50 * MB
    if content_type.startswith(("image/", "video/", "audio/", "application/")):
        if content_size <= limit:
            return SendabilityResult(True, "")
        return SendabilityResult(False, f"Exceeds {limit // MB}MB file size limit for Slack")

    return SendabilityResult(False, f"Unsupported file type '{content_type}' for Slack")


FILE_SENDABILITY_CHECKERS: dict[str, Callable[[str, int], SendabilityResult]] = {
    "whatsapp": can_send_on_whatsapp,
    "telegram": can_send_on_telegram,
    "slack": can_send_on_slack,
}

EMAIL_MAX_ATTACHMENT_BYTES = 20 * MB

EMAIL_BLOCKED_EXTENSIONS: frozenset[str] = frozenset(
    {
        "exe",
        "bat",
        "cmd",
        "com",
        "scr",
        "ps1",
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

EMAIL_BLOCKED_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "application/x-msdownload",
        "application/x-msdos-program",
        "application/x-bat",
        "application/x-sh",
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
EMAIL_TEXT_LIKE_APPLICATION_TYPES: frozenset[str] = frozenset(
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


def can_send_on_email(content_type: str, content_size: int) -> SendabilityResult:
    """Email: 20 MB cap, executable/installer denylist applies."""
    content_type = (content_type or "").split(";", 1)[0].strip().lower()
    if not content_size or content_size <= 0:
        return SendabilityResult(False, "File size unknown")
    if content_type in EMAIL_BLOCKED_CONTENT_TYPES:
        return SendabilityResult(False, f"File type '{content_type}' not allowed for email")
    if content_size > EMAIL_MAX_ATTACHMENT_BYTES:
        return SendabilityResult(False, "Exceeds 20MB email attachment limit")
    return SendabilityResult(True, "")


FILE_SENDABILITY_CHECKERS["email"] = can_send_on_email
