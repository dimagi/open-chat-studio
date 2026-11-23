"""Google's Decisions API, which answers typed questions about an input with probabilities instead of generating text.

One request carries several named questions; each answer comes back under its question's name.
See https://aistudio.google.com/docs/decisions-api.
"""

import json
from collections.abc import Mapping
from typing import Any

import httpx

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
    ChoiceQuestion,
    DecisionAuthenticationError,
    DecisionBackend,
    DecisionError,
    DecisionLimits,
    DecisionResponse,
    DecisionUsage,
    Question,
    ScoreQuestion,
    YesNoAnswer,
    YesNoQuestion,
)

GOOGLE_DECISIONS_URL = "https://generativelanguage.googleapis.com/v1beta/decisions"
GOOGLE_DECISIONS_TIMEOUT = 10.0

# Google asks callers to retry on these.
RETRY_STATUSES = frozenset({429, 503})

# Google answers an invalid API key with a 400, not a 401, and names this reason in the error's details.
INVALID_KEY_REASON = "API_KEY_INVALID"

# Google rejects control characters and any space but an ASCII one inside an option name, or a space at either end.
_OPTION_CHARACTER = r"[^\s\x00-\x1f\x7f-\x9f]"
OPTION_NAME_PATTERN = rf"{_OPTION_CHARACTER}(?:(?:{_OPTION_CHARACTER}| )*{_OPTION_CHARACTER})?"

GOOGLE_DECISION_LIMITS = DecisionLimits(
    max_options=255,
    min_levels=2,
    max_levels=255,
    max_questions=1000,
    min_options=2,
    key_pattern=r"[A-Za-z0-9_-]{1,64}",
    max_option_length=128,
    option_pattern=OPTION_NAME_PATTERN,
    requires_instructions=True,
)


class GoogleDecisionBackend(DecisionBackend):
    """Asks Google's Decisions API, sending the input as one JSON text block."""

    provider_type = "google"
    name = "Google Decisions"
    limits = GOOGLE_DECISION_LIMITS

    def __init__(self, api_key: str, url: str = GOOGLE_DECISIONS_URL, timeout: float = GOOGLE_DECISIONS_TIMEOUT):
        self.api_key = api_key
        self.url = url
        self.timeout = timeout

    def ask(self, model: str, input: Any, questions: Mapping[str, Question]) -> DecisionResponse:
        body = {"model": model, **_request(input, questions)}
        headers = {"x-goog-api-key": self.api_key}
        try:
            response = post_with_retry(self.url, body, headers, self.timeout, RETRY_STATUSES)
        except httpx.HTTPError as e:
            raise DecisionError(f"Could not reach Google: {type(e).__name__}: {e}") from e
        _raise_for_status(response)
        return _parse_response(model, response, questions)

    def request_text(self, input: Any, questions: Mapping[str, Question]) -> str:
        return json.dumps(_request(input, questions), ensure_ascii=False)


def _request(input: Any, questions: Mapping[str, Question]) -> dict:
    """The input and questions of a request. Google takes no JSON input, so the input is sent as its JSON text."""
    text = json.dumps(json_safe(input), ensure_ascii=False)
    return {
        "input": [{"type": "text", "text": text}],
        "questions": {name: _question(question) for name, question in questions.items()},
    }


def _question(question: Question) -> dict:
    instructions = question.instructions or ""
    match question:
        case ChoiceQuestion():
            criteria = {option: description or "" for option, description in question.options.items()}
            return {"type": "choice", "instructions": instructions, "criteria": criteria}
        case ScoreQuestion():
            return {"type": "score", "instructions": instructions, "criteria": list(question.levels)}
        case YesNoQuestion():
            return {"type": "noul", "instructions": instructions} | _yes_no_criteria(question)


def _yes_no_criteria(question: YesNoQuestion) -> dict:
    descriptions = {"true_description": question.true_description, "false_description": question.false_description}
    criteria = {key: text for key, text in descriptions.items() if text}
    return {"criteria": criteria} if criteria else {}


def _raise_for_status(response: httpx.Response) -> None:
    if response.is_success:
        return
    error = _error(response)
    code = _error_code(error)
    label = f" ({code})" if code else ""
    detail = str(error.get("message") or response.text or response.reason_phrase).strip()[:500]
    message = f"Google returned {response.status_code}{label}: {detail}"
    if response.status_code in (401, 403) or _names_an_invalid_key(error):
        raise DecisionAuthenticationError(message, response.status_code)
    raise DecisionError(message, response.status_code)


def _error(response: httpx.Response) -> dict:
    try:
        error = response.json().get("error")
    except (ValueError, AttributeError):
        return {}
    return error if isinstance(error, dict) else {}


def _error_code(error: dict) -> str | None:
    """Decision errors carry a code such as `safety`; errors from Google's API front end a number and a status."""
    code = error.get("code")
    return code if isinstance(code, str) else error.get("status")


def _names_an_invalid_key(error: dict) -> bool:
    details = error.get("details")
    if not isinstance(details, list):
        return False
    return any(isinstance(detail, dict) and detail.get("reason") == INVALID_KEY_REASON for detail in details)


def _parse_response(model: str, response: httpx.Response, questions: Mapping[str, Question]) -> DecisionResponse:
    """The answers to `questions`, leaving out any missing or malformed one. The response names no model."""
    try:
        data = response.json()
        raw_answers = dict(data["answers"])
        usage = data.get("usage") or {}
        usage = DecisionUsage(token_count(usage, "total_input_tokens"), token_count(usage, "total_output_tokens"))
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise DecisionError(f"Google returned a response that could not be read: {e}", response.status_code) from e
    answers = {name: answer for name, question in questions.items() if (answer := _answer(raw_answers, name, question))}
    return DecisionResponse(model, answers, usage, raw_answers=raw_answers)


def _answer(answers: Mapping[str, Any], name: str, question: Question) -> Answer | None:
    match question:
        case ChoiceQuestion():
            return read_answer(answers, name, "choice", choice_answer)
        case ScoreQuestion():
            return read_answer(answers, name, "score", score_answer)
        case YesNoQuestion():
            return read_answer(answers, name, "noul", _yes_no_answer)


def _yes_no_answer(answer: dict) -> YesNoAnswer:
    confidence = answer.get("confidence")
    return YesNoAnswer(probability(answer["noul"]), None if confidence is None else probability(confidence))
