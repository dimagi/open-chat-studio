from typing import Any

import httpx
from django.conf import settings

from apps.service_providers.auth_service.oauth import OAuthTokenError

DOCS_API_URL = "https://docs.googleapis.com/v1/documents"


def _reason_from_details(details: Any) -> str | None:
    if not isinstance(details, list):
        return None
    for detail in details:
        if isinstance(detail, dict) and detail.get("reason"):
            return detail["reason"]
    return None


def _reason_from_error(error: dict[str, Any]) -> str | None:
    return _reason_from_details(error.get("errors")) or _reason_from_details(error.get("details"))


def _error_payload(response: httpx.Response) -> dict[str, Any] | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    return error if isinstance(error, dict) else None


def _google_api_error_message(response: httpx.Response) -> str:
    """Return a concise, token-free description of a Google API error."""
    http_status = response.status_code
    error = _error_payload(response)
    if error is None:
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
