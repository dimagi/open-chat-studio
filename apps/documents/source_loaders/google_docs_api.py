from typing import Any

import httpx
from django.conf import settings

from apps.documents.source_loaders.google_docs_errors import _google_api_error_message
from apps.service_providers.auth_service.oauth import OAuthTokenError

DOCS_API_URL = "https://docs.googleapis.com/v1/documents"


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


def _request_google_document(auth_provider: Any, document_id: str) -> httpx.Response:
    try:
        headers = auth_provider.get_auth_service().get_auth_headers()
        return httpx.get(
            f"{DOCS_API_URL}/{document_id}",
            params={"includeTabsContent": "true"},
            headers=headers,
            timeout=settings.RESTRICTED_HTTP_MAX_TIMEOUT,
        )
    except (OAuthTokenError, ValueError):
        raise
    except httpx.HTTPError as exc:
        raise ValueError(f"Google Docs request failed: {exc}") from exc
    except Exception as exc:
        raise ValueError(f"Google Docs response was invalid: {exc}") from exc


def fetch_google_document(auth_provider: Any, document_id: str) -> dict[str, Any]:
    return _decode_document_response(_request_google_document(auth_provider, document_id))
