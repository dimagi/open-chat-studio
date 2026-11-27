"""OpenAI's Decisions API, which answers typed questions about an input with probabilities instead of generating text.

One request carries a list of questions; each answer comes back with its question's name, in the same order.
See https://developers.openai.com/api/docs/guides/decisions.
"""

import json
from collections.abc import Mapping, Sequence
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

OPENAI_API_BASE = "https://api.openai.com/v1"
OPENAI_DECISIONS_TIMEOUT = 10.0

RETRY_STATUSES = frozenset({429, 500, 503})

OPENAI_DECISION_LIMITS = DecisionLimits(max_options=255, min_options=2, min_levels=2, max_levels=10, max_questions=400)


class OpenAIDecisionBackend(DecisionBackend):
    """Asks OpenAI's Decisions API, sending the input as its JSON text."""

    provider_type = "openai"
    name = "OpenAI Decisions"
    limits = OPENAI_DECISION_LIMITS

    def __init__(
        self,
        api_key: str,
        base_url: str = OPENAI_API_BASE,
        organization: str | None = None,
        timeout: float = OPENAI_DECISIONS_TIMEOUT,
    ):
        self.api_key = api_key
        self.url = f"{base_url.rstrip('/')}/decisions"
        self.organization = organization
        self.timeout = timeout

    def ask(self, model: str, input: Any, questions: Mapping[str, Question]) -> DecisionResponse:
        body = {"model": model, **_request(input, questions)}
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if self.organization:
            headers["OpenAI-Organization"] = self.organization
        try:
            response = post_with_retry(self.url, body, headers, self.timeout, RETRY_STATUSES)
        except httpx.HTTPError as e:
            raise DecisionError(f"Could not reach OpenAI: {type(e).__name__}: {e}") from e
        _raise_for_status(response)
        return _parse_response(model, response, questions)

    def request_text(self, input: Any, questions: Mapping[str, Question]) -> str:
        return json.dumps(_request(input, questions), ensure_ascii=False)


def _request(input: Any, questions: Mapping[str, Question]) -> dict:
    """The input and questions of a request.

    The input goes as JSON text rather than an `input_json` part, which has nesting, value-count and
    size limits that a session's data can exceed.
    """
    return {
        "input": json.dumps(json_safe(input), ensure_ascii=False),
        "questions": [_question(name, question) for name, question in questions.items()],
    }


def _question(name: str, question: Question) -> dict:
    match question:
        case ChoiceQuestion():
            choices = [_described({"value": option}, text) for option, text in question.options.items()]
            return {"type": "choice", "name": name, "instructions": question.instructions or "", "choices": choices}
        case ScoreQuestion():
            levels = [{"label": level} for level in question.levels]
            return {"type": "score", "name": name, "instructions": question.instructions or "", "levels": levels}
        case YesNoQuestion():
            return {"type": "predicate", "name": name, "instructions": _yes_no_instructions(question)}


def _described(item: dict, description: str | None) -> dict:
    return item | {"description": description} if description else item


def _yes_no_instructions(question: YesNoQuestion) -> str:
    """The statement, then the descriptions of when it is true and false, which predicates have no fields for."""
    criteria = [
        f"{label} when: {text}"
        for label, text in (("True", question.true_description), ("False", question.false_description))
        if text
    ]
    parts = [question.instructions or "", "\n".join(criteria)]
    return "\n\n".join(part for part in parts if part)


def _raise_for_status(response: httpx.Response) -> None:
    if response.is_success:
        return
    error = _error(response)
    code = error.get("code") or error.get("type")
    label = f" ({code})" if isinstance(code, str) else ""
    detail = str(error.get("message") or response.text or response.reason_phrase).strip()[:500]
    message = f"OpenAI returned {response.status_code}{label}: {detail}"
    if response.status_code in (401, 403):
        raise DecisionAuthenticationError(message, response.status_code)
    raise DecisionError(message, response.status_code)


def _error(response: httpx.Response) -> dict:
    try:
        error = response.json().get("error")
    except (ValueError, AttributeError):
        return {}
    return error if isinstance(error, dict) else {}


def _parse_response(model: str, response: httpx.Response, questions: Mapping[str, Question]) -> DecisionResponse:
    """The answers to `questions`, leaving out any missing, refused or malformed one.

    The response's model can be a dated snapshot, so the requested name is kept for pricing.
    """
    try:
        data = response.json()
        answers = data["answers"]
        if not isinstance(answers, list):
            raise TypeError(f"answers is not a list: {type(answers).__name__}")
        usage = _usage(data.get("usage") or {})
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise DecisionError(f"OpenAI returned a response that could not be read: {e}", response.status_code) from e
    raw_answers = _answers_by_name(answers, list(questions))
    parsed = {name: answer for name, question in questions.items() if (answer := _answer(raw_answers, name, question))}
    return DecisionResponse(model, parsed, usage, raw_answers=raw_answers)


def _usage(usage: dict) -> DecisionUsage:
    """The token counts. OpenAI's `input_tokens` already includes the cached and cache-write counts."""
    details = usage.get("input_tokens_details") or {}
    return DecisionUsage(
        token_count(usage, "input_tokens"),
        token_count(usage, "output_tokens"),
        cached_input_tokens=token_count(details, "cached_tokens"),
        cache_write_tokens=token_count(details, "cache_write_tokens"),
    )


def _answers_by_name(answers: list, names: Sequence[str]) -> dict[str, Any]:
    """Each answer under its question's name. An answer with no name is the question at its position."""
    by_name = {}
    for index, answer in enumerate(answers):
        if not isinstance(answer, dict):
            continue
        name = answer.get("name")
        if not isinstance(name, str):
            name = names[index] if index < len(names) else None
        if name is not None:
            by_name.setdefault(name, answer)
    return by_name


def _answer(answers: Mapping[str, Any], name: str, question: Question) -> Answer | None:
    match question:
        case ChoiceQuestion():
            return read_answer(answers, name, "choice", lambda answer: choice_answer(_keyed_probabilities(answer)))
        case ScoreQuestion():
            return read_answer(answers, name, "score", lambda answer: score_answer(_keyed_probabilities(answer)))
        case YesNoQuestion():
            return read_answer(answers, name, "predicate", _yes_no_answer)


def _yes_no_answer(answer: dict) -> YesNoAnswer:
    return YesNoAnswer(probability(answer["probability"]))


def _keyed_probabilities(answer: dict) -> dict:
    """`answer` with its list of `{value, probability}` entries as a map from value to probability."""
    entries = answer.get("probabilities") or []
    if not isinstance(entries, list):
        raise TypeError(f"probabilities is not a list: {type(entries).__name__}")
    return answer | {"probabilities": {str(entry["value"]): probability(entry["probability"]) for entry in entries}}
