from collections.abc import Callable
from typing import NamedTuple

MB = 1024 * 1024


class SendabilityResult(NamedTuple):
    supported: bool
    reason: str


def can_send_on_whatsapp(content_type: str, content_size: int) -> SendabilityResult:
    """Meta Cloud API limits: 5MB images, 16MB audio/video, 100MB documents (application/*)."""
    if not content_type or not content_size:
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
    if not content_type or not content_size:
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
    if not content_type or not content_size:
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
