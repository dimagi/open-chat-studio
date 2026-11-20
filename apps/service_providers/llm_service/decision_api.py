"""What the HTTP clients of the decision backends share: the retried POST, answer parsing and JSON clean-up."""

import json
import math
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx
from pydantic import BaseModel

from apps.service_providers.llm_service.decision_backend import ChoiceAnswer, ScoreAnswer

# One retry absorbs a brief spike without holding up the conversation that is waiting on the answer.
RETRY_DELAY_SECONDS = 1.0
MAX_RETRY_DELAY_SECONDS = 5.0

# The strictest nesting and number rules of the decision APIs: Jev rejects deeper nesting, and numbers
# it cannot represent exactly.
MAX_NESTING = 64
MAX_EXACT_INT = 2**53


def post_with_retry(
    url: str, body: dict, headers: dict[str, str], timeout: float, retry_statuses: frozenset[int]
) -> httpx.Response:
    """POST `body` as JSON, and once more after a bounded wait when the status is in `retry_statuses`.

    Raises `httpx.HTTPError` when the provider cannot be reached.
    """
    with httpx.Client(timeout=timeout) as client:
        response = client.post(url, json=body, headers=headers)
        if response.status_code in retry_statuses:
            time.sleep(retry_delay(response))
            response = client.post(url, json=body, headers=headers)
    return response


def retry_delay(response: httpx.Response) -> float:
    try:
        delay = float(response.headers.get("retry-after", RETRY_DELAY_SECONDS))
    except ValueError:
        delay = RETRY_DELAY_SECONDS
    return min(max(delay, 0.0), MAX_RETRY_DELAY_SECONDS)


def read_answer[T](answers: Mapping[str, Any], name: str, question_type: str, parse: Callable[[dict], T]) -> T | None:
    """The answer `name` parsed by `parse`, or None when it is missing, of another type or malformed."""
    answer = answers.get(name)
    if not isinstance(answer, dict) or answer.get("type") != question_type:
        return None
    try:
        return parse(answer)
    except (KeyError, TypeError, ValueError):
        return None


def choice_answer(answer: dict) -> ChoiceAnswer:
    probabilities = dict(answer.get("probabilities") or {})
    return ChoiceAnswer(str(answer["choice"]), probability(answer["confidence"]), probabilities)


def score_answer(answer: dict) -> ScoreAnswer:
    probabilities = dict(answer.get("probabilities") or {})
    return ScoreAnswer(finite(answer["score"]), probability(answer["confidence"]), probabilities)


def finite(value: Any) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"not a finite number: {value!r}")
    return number


def probability(value: Any) -> float:
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise ValueError(f"not between 0 and 1: {value!r}")
    return number


def token_count(usage: dict, key: str) -> int:
    """The count the provider reported under `key`, or 0 when it reported none."""
    value = usage.get(key, 0)
    # `type` rather than `isinstance`: bool is a subclass of int, and `true` is not a count.
    if type(value) is not int or value < 0:
        raise ValueError(f"invalid {key}: {value!r}")
    return value


def json_safe(value: Any, depth: int = 0) -> Any:
    """`value` as JSON every decision API accepts.

    Non-finite floats become null and integers too large to be exact become strings. A container
    nested past the limit is sent as its JSON text, and anything JSON has no type for as its str().
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
