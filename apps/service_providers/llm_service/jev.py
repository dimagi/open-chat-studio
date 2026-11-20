"""Client for TypeSafe's Jev System One API.

Jev answers typed questions about a supplied `state` instead of generating text. One request
can carry several named questions; each answer comes back under its question's name.
See https://docs.typesafe.ai/api/system-one.
"""

import json
import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel

from apps.service_providers.llm_service import decision_backend
from apps.service_providers.llm_service.decision_backend import (
    Answer,
    ChoiceAnswer,
    DecisionAuthenticationError,
    DecisionBackend,
    DecisionError,
    DecisionLimits,
    DecisionResponse,
    DecisionUsage,
    ScoreAnswer,
    YesNoAnswer,
)

DEFAULT_JEV_API_BASE = "https://api.typesafe.ai"
JEV_ROUTER_ONLY_MESSAGE = "Jev can only be used by the LLM Router and Decision nodes"
SYSTEM_ONE_PATH = "/v1/systemone"
JEV_TIMEOUT_SECONDS = 10.0

# Jev asks callers to back off and retry on these. One retry absorbs a brief spike without
# holding up the conversation that is waiting on the answer.
RETRY_STATUSES = frozenset({429, 529})
RETRY_DELAY_SECONDS = 1.0
MAX_RETRY_DELAY_SECONDS = 5.0

# A choice question takes 1 to 255 options and a score question 1 to 10 levels. Jev sets no limit on
# the number of questions in one request.
JEV_LIMITS = DecisionLimits(max_options=255, min_levels=1, max_levels=10)

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
class ScoreQuestion:
    """Answered with a score on the rubric `criteria`, one description per level from lowest to highest."""

    criteria: Sequence[str]
    instructions: str | None = None

    def to_json(self) -> dict:
        return {"type": "score", "instructions": self.instructions, "criteria": list(self.criteria)}


@dataclass(frozen=True)
class NoulQuestion:
    """Answered with the probability that the statement in `instructions` is true.

    `criteria`, when given, has a "true" and a "false" key, each saying what that answer means or None.
    """

    instructions: str | None = None
    criteria: Mapping[str, str | None] | None = None

    def to_json(self) -> dict:
        criteria = dict(self.criteria) if self.criteria is not None else None
        return {"type": "noul", "instructions": self.instructions, "criteria": criteria}


Question = ChoiceQuestion | ScoreQuestion | NoulQuestion


@dataclass(frozen=True)
class JevResponse:
    model: str
    answers: dict[str, Any]
    usage: DecisionUsage

    def choice(self, name: str) -> ChoiceAnswer | None:
        """The answer to the choice question `name`, or None when there is no well-formed one."""
        return self._read(name, "choice", _choice_answer)

    def score(self, name: str) -> ScoreAnswer | None:
        """The answer to the score question `name`, or None when there is no well-formed one."""
        return self._read(name, "score", _score_answer)

    def noul(self, name: str) -> YesNoAnswer | None:
        """The answer to the noul question `name`, or None when there is no well-formed one."""
        return self._read(name, "noul", lambda answer: YesNoAnswer(probability=_probability(answer["noul"])))

    def _read[T](self, name: str, question_type: str, parse: Callable[[dict], T]) -> T | None:
        answer = self.answers.get(name)
        if not isinstance(answer, dict) or answer.get("type") != question_type:
            return None
        try:
            return parse(answer)
        except (KeyError, TypeError, ValueError):
            return None


def _choice_answer(answer: dict) -> ChoiceAnswer:
    if not isinstance(answer["choice"], str):
        raise TypeError(f"not a string: {answer['choice']!r}")
    probabilities = dict(answer.get("probabilities") or {})
    return ChoiceAnswer(answer["choice"], _probability(answer["confidence"]), probabilities)


def _score_answer(answer: dict) -> ScoreAnswer:
    probabilities = dict(answer.get("probabilities") or {})
    return ScoreAnswer(_finite(answer["score"]), _probability(answer["confidence"]), probabilities)


def _finite(value: Any) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"not a finite number: {value!r}")
    return number


def _probability(value: Any) -> float:
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise ValueError(f"not between 0 and 1: {value!r}")
    return number


class JevClient:
    def __init__(self, api_key: str, base_url: str = DEFAULT_JEV_API_BASE, timeout: float = JEV_TIMEOUT_SECONDS):
        self.api_key = api_key
        self.url = base_url.rstrip("/") + SYSTEM_ONE_PATH
        self.timeout = timeout

    def ask(self, model: str, state: Any, questions: Mapping[str, Question]) -> JevResponse:
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
            usage=DecisionUsage(_token_count(usage, "input_tokens"), _token_count(usage, "output_tokens")),
        )
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise JevError(f"Jev returned a response that could not be read: {e}", response.status_code) from e


def _token_count(usage: dict, key: str) -> int:
    """The count Jev reported under `key`, or 0 when it reported none."""
    value = usage.get(key, 0)
    # `type` rather than `isinstance`: bool is a subclass of int, and `true` is not a count.
    if type(value) is not int or value < 0:
        raise ValueError(f"invalid {key}: {value!r}")
    return value


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


class JevBackend(DecisionBackend):
    """Asks Jev, sending the input as Jev's `state`."""

    provider_type = "jev"
    name = "Jev"
    limits = JEV_LIMITS

    def __init__(self, client: JevClient):
        self.client = client

    def ask(self, model: str, input: Any, questions: Mapping[str, decision_backend.Question]) -> DecisionResponse:
        try:
            response = self.client.ask(model, input, _jev_questions(questions))
        except JevAuthenticationError as e:
            raise DecisionAuthenticationError(str(e), e.status_code) from e
        except JevError as e:
            raise DecisionError(str(e), e.status_code) from e
        answers = {
            name: answer for name, question in questions.items() if (answer := _answer(response, name, question))
        }
        return DecisionResponse(response.model, answers, response.usage, raw_answers=response.answers)

    def request_text(self, input: Any, questions: Mapping[str, decision_backend.Question]) -> str:
        jev_questions = {name: question.to_json() for name, question in _jev_questions(questions).items()}
        return json.dumps({"state": json_safe(input), "questions": jev_questions})


def _jev_questions(questions: Mapping[str, decision_backend.Question]) -> dict[str, Question]:
    return {name: _jev_question(question) for name, question in questions.items()}


def _jev_question(question: decision_backend.Question) -> Question:
    match question:
        case decision_backend.ChoiceQuestion():
            return ChoiceQuestion(criteria=dict(question.options), instructions=question.instructions)
        case decision_backend.ScoreQuestion():
            return ScoreQuestion(criteria=list(question.levels), instructions=question.instructions)
        case decision_backend.YesNoQuestion():
            criteria = {"true": question.true_description, "false": question.false_description}
            return NoulQuestion(question.instructions, criteria if any(criteria.values()) else None)


def _answer(response: JevResponse, name: str, question: decision_backend.Question) -> Answer | None:
    match question:
        case decision_backend.ChoiceQuestion():
            return response.choice(name)
        case decision_backend.ScoreQuestion():
            return response.score(name)
        case decision_backend.YesNoQuestion():
            return response.noul(name)
