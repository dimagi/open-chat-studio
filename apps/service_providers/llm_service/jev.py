"""Client for TypeSafe's Jev System One API.

Jev answers typed questions about a supplied `state` instead of generating text. One request
can carry several named questions; each answer comes back under its question's name.
See https://docs.typesafe.ai/api/system-one.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from apps.service_providers.llm_service import decision_backend
from apps.service_providers.llm_service.decision_api import (
    choice_answer,
    json_safe,
    post_with_retry,
    probability,
    read_answer,
    score_answer,
    token_count,
)
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

# Jev asks callers to back off and retry on these.
RETRY_STATUSES = frozenset({429, 529})

# A choice question takes 1 to 255 options and a score question 1 to 10 levels. Jev sets no limit on
# the number of questions in one request.
JEV_LIMITS = DecisionLimits(max_options=255, min_levels=1, max_levels=10)


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
        return read_answer(self.answers, name, "choice", choice_answer)

    def score(self, name: str) -> ScoreAnswer | None:
        """The answer to the score question `name`, or None when there is no well-formed one."""
        return read_answer(self.answers, name, "score", score_answer)

    def noul(self, name: str) -> YesNoAnswer | None:
        """The answer to the noul question `name`, or None when there is no well-formed one."""
        return read_answer(self.answers, name, "noul", lambda answer: YesNoAnswer(probability(answer["noul"])))


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
            response = post_with_retry(self.url, body, headers, self.timeout, RETRY_STATUSES)
        except httpx.HTTPError as e:
            raise JevError(f"Could not reach Jev: {type(e).__name__}: {e}") from e
        _raise_for_status(response)
        return response


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
            usage=DecisionUsage(token_count(usage, "input_tokens"), token_count(usage, "output_tokens")),
        )
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise JevError(f"Jev returned a response that could not be read: {e}", response.status_code) from e


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
