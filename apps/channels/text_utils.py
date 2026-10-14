import re

import emoji

# The regex from https://stackoverflow.com/a/6041965 is used, but tweaked to remove capturing groups
URL_REGEX = r"(?:http|ftp|https):\/\/(?:[\w_-]+(?:(?:\.[\w_-]+)+))(?:[\w.,@?^=%&:\/~+#-]*[\w@?^=%&\/~+#-])"

# Matches [^2]: [citation_text](https://example.com)
MARKDOWN_REF_PATTERN = r"^\[(?P<ref>.+?)\]:\s*\[(?P<citation_text>[^\]]+)\]\((?P<citation_url>.*)\)"


def strip_urls_and_emojis(text: str) -> tuple[str, list[str]]:
    """Strips any URLs in `text` and appends them to the end of the text. Emoji's are filtered out"""
    text = emoji.replace_emoji(text, replace="")

    url_pattern = re.compile(URL_REGEX)
    urls = list(dict.fromkeys(url_pattern.findall(text)))
    text = url_pattern.sub("", text)

    return text, urls


def append_skipped_attachment_notes(message_text: str, skipped: list[dict]) -> str:
    """Append one bracketed line per skipped attachment to message_text so
    the LLM can surface the skip reasons to the user."""
    if not skipped:
        return message_text
    lines = [f"[Attachment {s['name']!r} ({_human_size(s['size'])}) skipped — {s['reason']}]" for s in skipped]
    suffix = "\n\n" + "\n".join(lines)
    return (message_text or "").rstrip() + suffix


def _human_size(num_bytes: int) -> str:
    if num_bytes <= 0:
        return "size unknown"
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"
