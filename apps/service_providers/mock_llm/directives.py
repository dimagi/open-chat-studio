"""Reading the response-shaping keywords out of a user message."""

import re
from dataclasses import dataclass

from .lorem import SENTENCE_COUNTS

DEFAULT_SLOW_SECONDS = 5.0
DEFAULT_ERROR_STATUS = 500
DEFAULT_LENGTH = "medium"
# A delay holds a request thread, so a typo like "slow 100000" must not strand one for a day.
MAX_SLOW_SECONDS = 120.0

# Error codes whose HTTP status is implied, so "error insufficient_quota" is enough.
CODE_STATUSES = {
    "insufficient_quota": 429,
    "credit_balance_exhausted": 429,
    "rate_limit_exceeded": 429,
    "invalid_api_key": 401,
    "model_not_found": 404,
    "context_length_exceeded": 400,
}
# The code to report when the directive named a status instead.
STATUS_CODES = {
    400: "invalid_request_error",
    401: "invalid_api_key",
    403: "permission_denied",
    404: "model_not_found",
    408: "request_timeout",
    429: "rate_limit_exceeded",
}

_LENGTH_RE = re.compile(rf"\b({'|'.join(SENTENCE_COUNTS)})\b", re.IGNORECASE)
_SLOW_RE = re.compile(r"\bslow\b(?:\s+(\d+(?:\.\d+)?))?", re.IGNORECASE)
# Only a 4xx or 5xx counts as a status, so "error 200" and "error 999" fall back to the
# default rather than reaching Django with a status it refuses to serve. The trailing
# boundary keeps "error 4291" from silently meaning "error 429".
_ERROR_RE = re.compile(rf"\berror\b(?:\s+([45]\d{{2}}\b|{'|'.join(CODE_STATUSES)}))?", re.IGNORECASE)


@dataclass(frozen=True)
class ErrorDirective:
    status: int
    code: str


@dataclass(frozen=True)
class Directives:
    length: str = DEFAULT_LENGTH
    delay_seconds: float = 0.0
    error: ErrorDirective | None = None


def parse_directives(text: str) -> Directives:
    """Read the response-shaping keywords out of a user message."""
    lengths = _LENGTH_RE.findall(text)
    return Directives(
        # Last one wins, so "short, no wait, make it long" does what it says.
        length=lengths[-1].lower() if lengths else DEFAULT_LENGTH,
        delay_seconds=_parse_delay(text),
        error=_parse_error(text),
    )


def _parse_delay(text: str) -> float:
    match = _SLOW_RE.search(text)
    if not match:
        return 0.0
    seconds = float(match.group(1)) if match.group(1) else DEFAULT_SLOW_SECONDS
    return min(seconds, MAX_SLOW_SECONDS)


def _parse_error(text: str) -> ErrorDirective | None:
    match = _ERROR_RE.search(text)
    if not match:
        return None
    argument = (match.group(1) or "").lower()
    if argument in CODE_STATUSES:
        return ErrorDirective(status=CODE_STATUSES[argument], code=argument)
    status = int(argument) if argument else DEFAULT_ERROR_STATUS
    return ErrorDirective(status=status, code=_code_for_status(status))


def _code_for_status(status: int) -> str:
    if code := STATUS_CODES.get(status):
        return code
    return "server_error" if status >= 500 else "invalid_request_error"
