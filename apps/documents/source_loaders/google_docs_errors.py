from typing import Any

import httpx


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
