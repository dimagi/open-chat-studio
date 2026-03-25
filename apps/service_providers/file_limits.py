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
