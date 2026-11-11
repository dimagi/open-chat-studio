import re
from urllib.parse import urlparse

_DOCUMENT_PATH = re.compile(r"^/document/d/([A-Za-z0-9_-]+)(?:/|$)")


def extract_document_id(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme != "https" or parsed.netloc.lower() != "docs.google.com":
        raise ValueError("Enter an HTTPS Google Docs document URL.")
    match = _DOCUMENT_PATH.match(parsed.path)
    if not match:
        raise ValueError("Enter a Google Docs document URL, not a Drive, Sheets, Slides, or Forms URL.")
    return match.group(1)
