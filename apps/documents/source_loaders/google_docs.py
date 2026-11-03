import hashlib
import re
from collections.abc import Iterator
from typing import Any, Self
from urllib.parse import urlparse

import httpx
from django.conf import settings

from apps.documents.datamodels import GoogleDocsSourceConfig
from apps.documents.models import Collection, CollectionFile, DocumentSource
from apps.documents.source_loaders.base import BaseDocumentLoader, SourceDocument
from apps.documents.source_loaders.google_docs_content import extract_document_text
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


def _reason_from_details(details: Any) -> str | None:
    if not isinstance(details, list):
        return None
    for detail in details:
        if isinstance(detail, dict) and detail.get("reason"):
            return detail["reason"]
    return None


def _reason_from_error(error: dict[str, Any]) -> str | None:
    return _reason_from_details(error.get("errors")) or _reason_from_details(error.get("details"))


def _google_api_error_message(response: httpx.Response) -> str:
    """Return a concise, token-free description of a Google API error."""
    http_status = response.status_code
    try:
        payload = response.json()
    except ValueError:
        payload = None
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return f"Google Docs request failed ({http_status}): HTTP error"
    google_status = error.get("status") or "unknown status"
    reason = _reason_from_error(error)
    suffix = f", reason={reason}" if reason else ""
    message = error.get("message") or "Google API error"
    return f"Google Docs request failed ({http_status}, {google_status}{suffix}): {message}"


def _decode_document_response(response: httpx.Response) -> dict[str, Any]:
    if response.is_error:
        raise ValueError(_google_api_error_message(response))
    try:
        document = response.json()
    except ValueError as exc:
        raise ValueError("Google Docs API returned an invalid document") from exc
    if not isinstance(document, dict):
        raise ValueError("Google Docs API returned an invalid document")
    return document


def _fetch_google_document(auth_provider: Any, document_id: str) -> dict[str, Any]:
    try:
        headers = auth_provider.get_auth_service().get_auth_headers()
        response = httpx.get(
            f"{DOCS_API_URL}/{document_id}",
            params={"includeTabsContent": "true"},
            headers=headers,
            timeout=settings.RESTRICTED_HTTP_MAX_TIMEOUT,
        )
        return _decode_document_response(response)
    except OAuthTokenError:
        raise
    except httpx.HTTPError as exc:
        raise ValueError(f"Google Docs request failed: {exc}") from exc
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Google Docs response was invalid: {exc}") from exc


def _source_document(document: dict[str, Any], document_id: str, collection_id: int) -> SourceDocument:
    content = extract_document_text(document)
    if not content.strip():
        raise ValueError("Google Docs document contains no extractable text")
    content_bytes = content.encode("utf-8")
    title = document.get("title") or document_id
    source_url = f"https://docs.google.com/document/d/{document_id}/edit"
    metadata = {
        "collection_id": collection_id,
        "source_type": "google_docs",
        "document_id": document_id,
        "title": title,
        "filename": safe_document_filename(title, document_id),
        "source": source_url,
        "citation_url": source_url,
        "content_hash": hashlib.sha256(content_bytes).hexdigest(),
    }
    if document.get("revisionId"):
        metadata["revision_id"] = document["revisionId"]
    return SourceDocument(content=content_bytes, metadata=metadata)


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
        document = _fetch_google_document(self.auth_provider, document_id)
        yield _source_document(document, document_id, self.collection.id)

    def get_document_identifier(self, document: SourceDocument) -> str:
        return document.metadata["document_id"]

    def should_update_document(self, document: SourceDocument, existing_file: CollectionFile) -> bool:
        existing_metadata = existing_file.file.metadata or {}
        content_changed = document.metadata.get("content_hash") != existing_metadata.get("content_hash")
        filename_changed = document.metadata.get("filename") != existing_file.file.name
        title_changed = document.metadata.get("title") != existing_metadata.get("title")
        return any((content_changed, filename_changed, title_changed))
