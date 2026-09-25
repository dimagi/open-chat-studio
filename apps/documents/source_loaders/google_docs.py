import hashlib
import re
import unicodedata
from collections.abc import Iterator
from typing import Any, Self
from urllib.parse import urlparse

import httpx
from django.conf import settings

from apps.documents.datamodels import GoogleDocsSourceConfig
from apps.documents.models import Collection, CollectionFile, DocumentSource
from apps.documents.source_loaders.base import BaseDocumentLoader, SourceDocument
from apps.service_providers.auth_service.oauth import OAuthTokenError
from apps.service_providers.models import AuthProviderType

DOCS_API_URL = "https://docs.googleapis.com/v1/documents"
_DOCUMENT_PATH = re.compile(r"^/document/d/([A-Za-z0-9_-]+)(?:/|$)")


def safe_document_filename(title: str, document_id: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", title).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return f"{(cleaned or document_id)[:200]}.txt"


def extract_document_id(value: str) -> str:
    value = value.strip()
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.netloc.lower() != "docs.google.com":
        raise ValueError("Enter an HTTPS Google Docs document URL.")
    match = _DOCUMENT_PATH.match(parsed.path)
    if not match:
        raise ValueError("Enter a Google Docs document URL, not a Drive, Sheets, Slides, or Forms URL.")
    return match.group(1)


def _text_from_content(content: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for element in content:
        if paragraph := element.get("paragraph"):
            text = "".join(run.get("textRun", {}).get("content", "") for run in paragraph.get("elements", []))
            style = paragraph.get("paragraphStyle", {}).get("namedStyleType", "")
            if style.startswith("HEADING_") and text.strip():
                text = "# " + text
            bullet = paragraph.get("bullet")
            if bullet is not None and text.strip():
                text = "- " + text
            lines.append(text.rstrip("\n"))
        elif table := element.get("table"):
            for row in table.get("tableRows", []):
                cells = [
                    " ".join(_text_from_content(cell.get("content", []))).strip() for cell in row.get("tableCells", [])
                ]
                lines.append(" | ".join(cells))
        elif toc := element.get("tableOfContents"):
            lines.extend(_text_from_content(toc.get("content", [])))
        elif element.get("sectionBreak"):
            if lines and lines[-1] != "":
                lines.append("")
    return lines


def normalize_document_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(char if char in "\n\t" or unicodedata.category(char) not in {"Cc", "Cf"} else " " for char in text)


def extract_document_text(document: dict[str, Any]) -> str:
    lines: list[str] = []

    def visit_tab(tab: dict[str, Any], is_child: bool = False) -> None:
        properties = tab.get("tabProperties", {})
        title = properties.get("title")
        if title and (is_child or len(document.get("tabs", [])) > 1):
            lines.extend([f"## {title}", ""])
        body = tab.get("documentTab", {}).get("body", {})
        lines.extend(_text_from_content(body.get("content", [])))
        for child in tab.get("childTabs", []):
            visit_tab(child, True)

    tabs = document.get("tabs") or []
    if tabs:
        for tab in tabs:
            visit_tab(tab)
    else:
        lines.extend(_text_from_content(document.get("body", {}).get("content", [])))
    text = normalize_document_text("\n".join(lines)).strip()
    return f"{text}\n" if text else ""


def _google_api_error_message(response: httpx.Response) -> str:
    """Return a concise, token-free description of a Google API error."""
    http_status = response.status_code
    try:
        payload = response.json()
    except ValueError:
        return f"Google Docs request failed ({http_status}): HTTP error"

    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return f"Google Docs request failed ({http_status}): HTTP error"

    google_status = error.get("status") or "unknown status"
    message = error.get("message") or "Google API error"
    reason = None
    reasons = error.get("errors")
    if isinstance(reasons, list):
        for detail in reasons:
            if isinstance(detail, dict) and detail.get("reason"):
                reason = detail["reason"]
                break
    details = error.get("details")
    if not reason and isinstance(details, list):
        for detail in details:
            if isinstance(detail, dict) and detail.get("reason"):
                reason = detail["reason"]
                break

    suffix = f", reason={reason}" if reason else ""
    return f"Google Docs request failed ({http_status}, {google_status}{suffix}): {message}"


class GoogleDocsDocumentLoader(BaseDocumentLoader[GoogleDocsSourceConfig]):
    @classmethod
    def for_document_source(cls, collection: Collection, document_source: DocumentSource) -> Self:
        provider = document_source.auth_provider
        if not provider:
            raise ValueError("Google Docs document source requires an OAuth Authorization Code provider")
        if provider.team_id != document_source.team_id:
            raise ValueError("Google Docs authentication provider must belong to the same team")
        if provider.type != AuthProviderType.oauth_authorization_code:
            raise ValueError("Google Docs document source requires an OAuth Authorization Code provider")
        return cls(collection, document_source.config.google_docs, provider)

    def load_documents(self) -> Iterator[SourceDocument]:
        document_id = extract_document_id(self.config.document_url)
        try:
            headers = self.auth_provider.get_auth_service().get_auth_headers()
            response = httpx.get(
                f"{DOCS_API_URL}/{document_id}",
                params={"includeTabsContent": "true"},
                headers=headers,
                timeout=settings.RESTRICTED_HTTP_MAX_TIMEOUT,
            )
            if response.is_error:
                raise ValueError(_google_api_error_message(response))
            document = response.json()
        except OAuthTokenError:
            raise
        except httpx.HTTPError as exc:
            raise ValueError(f"Google Docs request failed: {exc}") from exc
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"Google Docs response was invalid: {exc}") from exc
        if not isinstance(document, dict):
            raise ValueError("Google Docs API returned an invalid document")
        content = extract_document_text(document)
        if not content.strip():
            raise ValueError("Google Docs document contains no extractable text")
        content_bytes = content.encode("utf-8")
        title = document.get("title") or document_id
        metadata = {
            "collection_id": self.collection.id,
            "source_type": "google_docs",
            "document_id": document_id,
            "title": title,
            "filename": safe_document_filename(title, document_id),
            "source": f"https://docs.google.com/document/d/{document_id}/edit",
            "citation_url": f"https://docs.google.com/document/d/{document_id}/edit",
            "content_hash": hashlib.sha256(content_bytes).hexdigest(),
        }
        if document.get("revisionId"):
            metadata["revision_id"] = document["revisionId"]
        yield SourceDocument(content=content_bytes, metadata=metadata)

    def get_document_identifier(self, document: SourceDocument) -> str:
        return document.metadata["document_id"]

    def should_update_document(self, document: SourceDocument, existing_file: CollectionFile) -> bool:
        content_changed = document.metadata.get("content_hash") != (existing_file.file.metadata or {}).get(
            "content_hash"
        )
        filename_changed = document.metadata.get("filename") != existing_file.file.name
        return content_changed or filename_changed
