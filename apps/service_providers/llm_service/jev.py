"""Client for TypeSafe's Jev System One API.

Jev answers typed questions about a supplied `state` instead of generating text. One request
can carry several named questions; each answer comes back under its question's name.
See https://docs.typesafe.ai/api/system-one.
"""

import json
import math
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel

DEFAULT_JEV_API_BASE = "https://api.typesafe.ai"
JEV_ROUTER_ONLY_MESSAGE = "Jev can only be used by the LLM Router node"
SYSTEM_ONE_PATH = "/v1/systemone"
JEV_TIMEOUT_SECONDS = 10.0

# Jev asks callers to back off and retry on these. One retry absorbs a brief spike without
# holding up the conversation that is waiting on the answer.
RETRY_STATUSES = frozenset({429, 529})
RETRY_DELAY_SECONDS = 1.0
MAX_RETRY_DELAY_SECONDS = 5.0

# Jev rejects deeper nesting in `state`, and numbers it cannot represent exactly.
MAX_NESTING = 64
MAX_EXACT_INT = 2**53


class JevError(Exception):
    """A Jev request that failed or returned something unusable."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class JevAuthenticationError(JevError):
    """Jev rejected the API key. Retrying will not help; the key has to be changed."""


@dataclass(frozen=True)
class ChoiceQuestion:
    """Answered with one of `criteria`'s keys. Each value says when its key applies, or is None."""

    criteria: Mapping[str, str | None]
    instructions: str | None = None

    def to_json(self) -> dict:
        return {"type": "choice", "instructions": self.instructions, "criteria": dict(self.criteria)}


@dataclass(frozen=True)
class ChoiceAnswer:
    choice: str
    confidence: float
    probabilities: dict[str, float]


@dataclass(frozen=True)
class JevUsage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class JevResponse:
    model: str
    answers: dict[str, Any]
    usage: JevUsage

    def choice(self, name: str) -> ChoiceAnswer | None:
        """The answer to the choice question `name`, or None when there is no well-formed one."""
        answer = self.answers.get(name)
        if not isinstance(answer, dict) or answer.get("type") != "choice":
            return None
        try:
            return ChoiceAnswer(
                choice=str(answer["choice"]),
                confidence=float(answer["confidence"]),
                probabilities=dict(answer.get("probabilities") or {}),
            )
        except (KeyError, TypeError, ValueError):
            return None


class JevClient:
    def __init__(self, api_key: str, base_url: str = DEFAULT_JEV_API_BASE, timeout: float = JEV_TIMEOUT_SECONDS):
        self.api_key = api_key
        self.url = base_url.rstrip("/") + SYSTEM_ONE_PATH
        self.timeout = timeout

    def ask(self, model: str, state: Any, questions: Mapping[str, ChoiceQuestion]) -> JevResponse:
        """Ask every question in `questions` about `state` in one request."""
        body = {
            "model": model,
            "state": json_safe(state),
            "questions": {name: question.to_json() for name, question in questions.items()},
        }
        return _parse_response(self._post(body))

    def _post(self, body: dict) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(self.url, json=body, headers=headers)
                if response.status_code in RETRY_STATUSES:
                    time.sleep(_retry_delay(response))
                    response = client.post(self.url, json=body, headers=headers)
        except httpx.HTTPError as e:
            raise JevError(f"Could not reach Jev: {type(e).__name__}: {e}") from e
        _raise_for_status(response)
        return response


def _retry_delay(response: httpx.Response) -> float:
    try:
        delay = float(response.headers.get("retry-after", RETRY_DELAY_SECONDS))
    except ValueError:
        delay = RETRY_DELAY_SECONDS
    return min(max(delay, 0.0), MAX_RETRY_DELAY_SECONDS)


def _raise_for_status(response: httpx.Response) -> None:
    if response.is_success:
        return
    message = f"Jev returned {response.status_code}: {_error_detail(response)}"
    if response.status_code in (401, 403):
        raise JevAuthenticationError(message, response.status_code)
    raise JevError(message, response.status_code)


def _error_detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    return str(detail or response.text or response.reason_phrase)[:500]


def _parse_response(response: httpx.Response) -> JevResponse:
    try:
        data = response.json()
        usage = data.get("usage") or {}
        return JevResponse(
            model=str(data.get("model", "")),
            answers=dict(data["answers"]),
            usage=JevUsage(int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)),
        )
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise JevError(f"Jev returned a response that could not be read: {e}", response.status_code) from e


def json_safe(value: Any, depth: int = 0) -> Any:
    """`value` as JSON Jev accepts.

    Non-finite floats become null and integers too large to be exact become strings. A container
    nested past Jev's limit is sent as its JSON text, and anything JSON has no type for as its str().
    """
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, Mapping | list | tuple | set | frozenset):
        return _container_json_safe(value, depth)
    return _scalar_json_safe(value)


def _container_json_safe(value: Mapping | list | tuple | set | frozenset, depth: int) -> Any:
    if depth >= MAX_NESTING:
        return json.dumps(value, default=str)
    if isinstance(value, Mapping):
        return {str(key): json_safe(item, depth + 1) for key, item in value.items()}
    return [json_safe(item, depth + 1) for item in value]


def _scalar_json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str | bool):
        return value
    if isinstance(value, int):
        return value if abs(value) <= MAX_EXACT_INT else str(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return str(value)
