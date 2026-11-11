"""Records custom action HTTP requests on the active trace, with credentials redacted."""

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from langchain_core.callbacks import dispatch_custom_event

from apps.service_providers.tracing.const import CUSTOM_ACTION_REQUEST_EVENT

logger = logging.getLogger("ocs.tools")

MAX_BODY_LENGTH = 10_000
REDACTED = "[REDACTED]"
ATTACHMENT_PLACEHOLDER = "[attachment not logged]"
SENSITIVE_HEADER_FRAGMENTS = ("auth", "token", "secret", "password", "key", "cookie", "session", "signature")


@dataclass
class RequestAttempt:
    """One HTTP request a custom action tool sent, and what came back."""

    tool_name: str
    custom_action_name: str | None
    request: httpx.Request
    # The request's headers before the auth provider added its own while sending it.
    unauthenticated_headers: dict[str, str]
    response: httpx.Response | None = None
    error: BaseException | None = None
    started: float = field(default_factory=time.monotonic)

    @property
    def duration_ms(self) -> int:
        return int((time.monotonic() - self.started) * 1000)


def log_request(attempt: RequestAttempt) -> None:
    """Send the request log to the trace callbacks of the tool run making the request."""
    try:
        dispatch_custom_event(CUSTOM_ACTION_REQUEST_EVENT, build_request_log(attempt))
    except RuntimeError:
        # dispatch_custom_event raises this when the call is not inside a tool run.
        pass
    except Exception:
        logger.exception("[%s] Could not log the request", attempt.tool_name)


def build_request_log(attempt: RequestAttempt) -> dict[str, Any]:
    """A JSON-ready record of one request and its response.

    Headers the auth provider added are found by comparing the sent headers with the
    unauthenticated ones; their values are removed from every field of the record.
    """
    request, response, error = attempt.request, attempt.response, attempt.error
    auth_headers = {
        name: value for name, value in request.headers.items() if attempt.unauthenticated_headers.get(name) != value
    }
    redactor = _Redactor(auth_headers)
    return {
        "tool": attempt.tool_name,
        "custom_action": attempt.custom_action_name,
        "method": request.method,
        "url": redactor.scrub(str(request.url)),
        "request_headers": redactor.headers(request.headers),
        "request_body": redactor.body(request.content.decode("utf-8", errors="replace") or None),
        "status_code": response.status_code if response is not None else None,
        "response_headers": redactor.headers(response.headers) if response is not None else {},
        "response_body": redactor.body(_response_text(response)) if response is not None else None,
        "duration_ms": attempt.duration_ms,
        "error": redactor.scrub(f"{type(error).__name__}: {error}") if error else "",
    }


def _response_text(response: httpx.Response) -> str | None:
    if response.headers.get("content-disposition", "").lower().startswith("attachment"):
        return ATTACHMENT_PLACEHOLDER
    try:
        return response.text
    except httpx.ResponseNotRead:
        return None


class _Redactor:
    def __init__(self, auth_headers: dict[str, str]):
        self.auth_header_names = set(auth_headers)
        # Also match the credential without its scheme ("Bearer", "Basic", "ApiKey"), as an API may echo it alone.
        secrets = set()
        for value in auth_headers.values():
            secrets.add(value.strip())
            secrets.add(value.partition(" ")[2].strip())
        self.secrets = sorted(filter(None, secrets), key=len, reverse=True)

    def headers(self, headers: httpx.Headers) -> dict[str, str]:
        return {name: REDACTED if self._is_sensitive(name) else self.scrub(value) for name, value in headers.items()}

    def body(self, text: str | None) -> str | None:
        if text is None or text == ATTACHMENT_PLACEHOLDER:
            return text
        text = self.scrub(text)
        if len(text) <= MAX_BODY_LENGTH:
            return text
        return f"{text[:MAX_BODY_LENGTH]}\n... [truncated {len(text) - MAX_BODY_LENGTH} characters]"

    def scrub(self, text: str) -> str:
        for secret in self.secrets:
            text = text.replace(secret, REDACTED)
        return text

    def _is_sensitive(self, name: str) -> bool:
        return name in self.auth_header_names or any(fragment in name for fragment in SENSITIVE_HEADER_FRAGMENTS)
