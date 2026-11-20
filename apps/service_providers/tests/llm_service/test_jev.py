import json
from decimal import Decimal
from unittest import mock

import httpx
import pytest
from pydantic import BaseModel

from apps.service_providers.llm_service import decision_backend
from apps.service_providers.llm_service.decision_backend import (
    DecisionAuthenticationError,
    DecisionError,
    DecisionUsage,
    ScoreAnswer,
    YesNoAnswer,
)
from apps.service_providers.llm_service.jev import (
    JEV_LIMITS,
    MAX_NESTING,
    ChoiceAnswer,
    ChoiceQuestion,
    JevAuthenticationError,
    JevBackend,
    JevClient,
    JevError,
    NoulQuestion,
    ScoreQuestion,
    json_safe,
)

URL = "https://api.typesafe.ai/v1/systemone"

ANSWER = {
    "model": "jev-1.14.0",
    "answers": {
        "route": {
            "type": "choice",
            "choice": "BILLING",
            "confidence": 0.82,
            "probabilities": {"BILLING": 0.9, "SUPPORT": 0.1},
        }
    },
    "usage": {"input_tokens": 120, "output_tokens": 12},
}

QUESTIONS = {"route": ChoiceQuestion(criteria={"BILLING": "Payments", "SUPPORT": None}, instructions="Pick a route.")}


@pytest.fixture(autouse=True)
def no_sleep():
    with mock.patch("apps.service_providers.llm_service.jev.time.sleep") as sleep:
        yield sleep


def _ask(state="I was charged twice"):
    return JevClient(api_key="test-key").ask("jev-latest", state, QUESTIONS)


def test_sends_the_model_state_and_questions(httpx_mock):
    httpx_mock.add_response(url=URL, method="POST", json=ANSWER)

    _ask()

    request = httpx_mock.get_request()
    assert request.headers["Authorization"] == "Bearer test-key"
    assert json.loads(request.content) == {
        "model": "jev-latest",
        "state": "I was charged twice",
        "questions": {
            "route": {
                "type": "choice",
                "instructions": "Pick a route.",
                "criteria": {"BILLING": "Payments", "SUPPORT": None},
            }
        },
    }


def test_reads_the_answer_and_usage(httpx_mock):
    httpx_mock.add_response(url=URL, json=ANSWER)

    response = _ask()

    assert response.model == "jev-1.14.0"
    assert response.choice("route") == ChoiceAnswer("BILLING", 0.82, {"BILLING": 0.9, "SUPPORT": 0.1})
    assert (response.usage.input_tokens, response.usage.output_tokens) == (120, 12)


def test_a_custom_base_url_is_used(httpx_mock):
    httpx_mock.add_response(url="http://localhost:9999/proxy/v1/systemone", json=ANSWER)

    JevClient(api_key="k", base_url="http://localhost:9999/proxy/").ask("jev-latest", "hi", QUESTIONS)


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(None, id="missing"),
        pytest.param({"type": "noul", "noul": 0.9}, id="other-type"),
        pytest.param({"type": "choice", "choice": "BILLING"}, id="no-confidence"),
        pytest.param({"type": "choice", "choice": "BILLING", "confidence": "high"}, id="bad-confidence"),
        pytest.param({"type": "choice", "choice": "BILLING", "confidence": "NaN"}, id="nan-confidence"),
        pytest.param({"type": "choice", "choice": "BILLING", "confidence": 1.5}, id="confidence-above-one"),
        pytest.param({"type": "choice", "choice": "BILLING", "confidence": -0.1}, id="negative-confidence"),
    ],
)
def test_a_missing_or_malformed_answer_reads_as_none(httpx_mock, answer):
    answers = {"route": answer} if answer else {"other": ANSWER["answers"]["route"]}
    httpx_mock.add_response(url=URL, json={**ANSWER, "answers": answers})

    assert _ask().choice("route") is None


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        pytest.param(
            ScoreQuestion(criteria=["Calm", "Annoyed", "Angry"], instructions="How frustrated?"),
            {"type": "score", "instructions": "How frustrated?", "criteria": ["Calm", "Annoyed", "Angry"]},
            id="score",
        ),
        pytest.param(
            NoulQuestion(criteria={"true": "Asks for money back", "false": None}),
            {"type": "noul", "instructions": None, "criteria": {"true": "Asks for money back", "false": None}},
            id="noul",
        ),
        pytest.param(
            NoulQuestion(instructions="Mentions a refund?"),
            {"type": "noul", "instructions": "Mentions a refund?", "criteria": None},
            id="noul-without-criteria",
        ),
    ],
)
def test_sends_score_and_noul_questions(httpx_mock, question, expected):
    httpx_mock.add_response(url=URL, json=ANSWER)

    JevClient(api_key="k").ask("jev-latest", "hi", {"q": question})

    assert json.loads(httpx_mock.get_request().content)["questions"] == {"q": expected}


def test_reads_score_and_noul_answers(httpx_mock):
    answers = {
        "frustration": {
            "type": "score",
            "score": 1.7,
            "confidence": 0.55,
            "legend": {"0": "Calm", "1": "Annoyed", "2": "Angry"},
            "probabilities": {"0": 0.1, "1": 0.1, "2": 0.8},
        },
        "refund": {"type": "noul", "noul": 0.98},
    }
    httpx_mock.add_response(url=URL, json={**ANSWER, "answers": answers})

    response = _ask()

    assert response.score("frustration") == ScoreAnswer(1.7, 0.55, {"0": 0.1, "1": 0.1, "2": 0.8})
    assert response.noul("refund") == YesNoAnswer(0.98)


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param({"type": "choice", "choice": "A", "confidence": 0.5}, id="other-type"),
        pytest.param({"type": "score", "confidence": 0.5}, id="no-score"),
        pytest.param({"type": "score", "score": "Infinity", "confidence": 0.5}, id="infinite-score"),
        pytest.param({"type": "score", "score": 1, "confidence": 2}, id="confidence-above-one"),
    ],
)
def test_a_malformed_score_answer_reads_as_none(httpx_mock, answer):
    httpx_mock.add_response(url=URL, json={**ANSWER, "answers": {"q": answer}})

    assert _ask().score("q") is None


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param({"type": "score", "score": 1, "confidence": 0.5}, id="other-type"),
        pytest.param({"type": "noul"}, id="no-noul"),
        pytest.param({"type": "noul", "noul": "yes"}, id="not-a-number"),
        pytest.param({"type": "noul", "noul": 1.2}, id="above-one"),
    ],
)
def test_a_malformed_noul_answer_reads_as_none(httpx_mock, answer):
    httpx_mock.add_response(url=URL, json={**ANSWER, "answers": {"q": answer}})

    assert _ask().noul("q") is None


@pytest.mark.parametrize("status_code", [pytest.param(429, id="rate-limited"), pytest.param(529, id="overloaded")])
def test_retries_once_when_asked_to_back_off(httpx_mock, no_sleep, status_code):
    httpx_mock.add_response(url=URL, status_code=status_code, headers={"retry-after": "2"})
    httpx_mock.add_response(url=URL, json=ANSWER)

    assert _ask().choice("route").choice == "BILLING"
    no_sleep.assert_called_once_with(2.0)


def test_gives_up_after_one_retry(httpx_mock):
    httpx_mock.add_response(url=URL, status_code=529, json={"detail": "Overloaded"})
    httpx_mock.add_response(url=URL, status_code=529, json={"detail": "Overloaded"})

    with pytest.raises(JevError, match="Jev returned 529: Overloaded") as excinfo:
        _ask()

    assert excinfo.value.status_code == 529
    assert len(httpx_mock.get_requests()) == 2


@pytest.mark.parametrize(
    ("retry_after", "expected"),
    [
        pytest.param("60", 5.0, id="capped"),
        pytest.param("soon", 1.0, id="unparseable"),
    ],
)
def test_the_retry_wait_is_bounded(httpx_mock, no_sleep, retry_after, expected):
    httpx_mock.add_response(url=URL, status_code=429, headers={"retry-after": retry_after})
    httpx_mock.add_response(url=URL, json=ANSWER)

    _ask()

    no_sleep.assert_called_once_with(expected)


@pytest.mark.parametrize("status_code", [pytest.param(401, id="unauthorized"), pytest.param(403, id="forbidden")])
def test_a_rejected_key_is_an_authentication_error(httpx_mock, status_code):
    httpx_mock.add_response(url=URL, status_code=status_code, json={"detail": "Invalid API key"})

    with pytest.raises(JevAuthenticationError, match="Invalid API key"):
        _ask()


@pytest.mark.parametrize(
    ("status_code", "body"),
    [
        pytest.param(400, {"detail": "max_tokens_exceeded"}, id="bad-request"),
        pytest.param(413, None, id="too-large"),
        pytest.param(422, {"detail": [{"loc": ["body", "state"], "msg": "Field required"}]}, id="invalid"),
        pytest.param(500, None, id="server-error"),
    ],
)
def test_other_failures_are_jev_errors_not_authentication_errors(httpx_mock, status_code, body):
    httpx_mock.add_response(url=URL, status_code=status_code, json=body)

    with pytest.raises(JevError) as excinfo:
        _ask()

    assert not isinstance(excinfo.value, JevAuthenticationError)
    assert excinfo.value.status_code == status_code


def test_a_network_failure_is_a_jev_error(httpx_mock):
    httpx_mock.add_exception(httpx.ReadTimeout("timed out"), url=URL)

    with pytest.raises(JevError, match="Could not reach Jev: ReadTimeout"):
        _ask()


@pytest.mark.parametrize(
    "input_tokens",
    [
        pytest.param("-1", id="negative"),
        pytest.param("true", id="boolean"),
        pytest.param("1.9", id="fractional"),
        pytest.param("1e999", id="overflowing"),
        pytest.param('"120"', id="string"),
    ],
)
def test_a_malformed_token_count_is_a_jev_error(httpx_mock, input_tokens):
    """`input_tokens` is the raw JSON literal, so values Python would not serialise can be sent too."""
    body = json.dumps({**ANSWER, "usage": {"input_tokens": "TOKENS", "output_tokens": 12}})
    httpx_mock.add_response(url=URL, content=body.replace('"TOKENS"', input_tokens).encode())

    with pytest.raises(JevError, match="input_tokens"):
        _ask()


def test_missing_token_counts_read_as_zero(httpx_mock):
    httpx_mock.add_response(url=URL, json={**ANSWER, "usage": {"input_tokens": 120}})

    usage = _ask().usage

    assert (usage.input_tokens, usage.output_tokens) == (120, 0)


def test_an_unreadable_body_is_a_jev_error(httpx_mock):
    httpx_mock.add_response(url=URL, content=b"<html>gateway</html>")

    with pytest.raises(JevError, match="could not be read"):
        _ask()


class _Attachment(BaseModel):
    file_id: int


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param({"a": float("nan"), "b": float("inf"), "c": 1.5}, {"a": None, "b": None, "c": 1.5}, id="floats"),
        pytest.param([2**60, 7], [str(2**60), 7], id="large-int"),
        pytest.param({1: Decimal("1.10")}, {"1": "1.10"}, id="key-and-decimal"),
        pytest.param({"files": [_Attachment(file_id=3)]}, {"files": [{"file_id": 3}]}, id="pydantic-model"),
        pytest.param(("x", True, None), ["x", True, None], id="tuple"),
    ],
)
def test_json_safe(value, expected):
    assert json_safe(value) == expected


def test_json_safe_flattens_containers_past_the_nesting_limit():
    value = inner = []
    for _ in range(MAX_NESTING + 5):
        inner.append([])
        inner = inner[0]

    result = json_safe(value)

    for _ in range(MAX_NESTING):
        result = result[0]
    assert isinstance(result, str)


NEUTRAL_QUESTIONS = {
    "route": decision_backend.ChoiceQuestion(options={"BILLING": "Payments", "SUPPORT": None}, instructions="Pick.")
}


def _ask_backend(state=None):
    return JevBackend(JevClient(api_key="test-key")).ask("jev-latest", state or {"input": "hi"}, NEUTRAL_QUESTIONS)


class TestJevBackend:
    def test_sends_a_neutral_choice_as_a_jev_choice(self, httpx_mock):
        httpx_mock.add_response(url=URL, json=ANSWER)

        _ask_backend({"input": "hi", "score": float("nan")})

        assert json.loads(httpx_mock.get_request().content) == {
            "model": "jev-latest",
            "state": {"input": "hi", "score": None},
            "questions": {
                "route": {"type": "choice", "instructions": "Pick.", "criteria": NEUTRAL_QUESTIONS["route"].options}
            },
        }

    def test_returns_neutral_answers_and_usage(self, httpx_mock):
        httpx_mock.add_response(url=URL, json=ANSWER)

        response = _ask_backend()

        assert response.model == "jev-1.14.0"
        assert response.choice("route") == ChoiceAnswer("BILLING", 0.82, {"BILLING": 0.9, "SUPPORT": 0.1})
        assert response.usage == DecisionUsage(120, 12)
        assert response.raw_answers == ANSWER["answers"]

    def test_a_malformed_answer_is_left_out(self, httpx_mock):
        httpx_mock.add_response(url=URL, json={**ANSWER, "answers": {"route": {"type": "choice", "choice": "X"}}})

        assert _ask_backend().answers == {}

    @pytest.mark.parametrize(
        ("status_code", "expected"),
        [
            pytest.param(401, DecisionAuthenticationError, id="unauthorized"),
            pytest.param(403, DecisionAuthenticationError, id="forbidden"),
            pytest.param(422, DecisionError, id="invalid"),
            pytest.param(500, DecisionError, id="server-error"),
        ],
    )
    def test_jev_errors_become_decision_errors(self, httpx_mock, status_code, expected):
        httpx_mock.add_response(url=URL, status_code=status_code, json={"detail": "nope"})

        with pytest.raises(DecisionError) as excinfo:
            _ask_backend()

        assert type(excinfo.value) is expected
        assert excinfo.value.status_code == status_code
        assert f"Jev returned {status_code}: nope" in str(excinfo.value)

    def test_a_network_failure_is_a_decision_error(self, httpx_mock):
        httpx_mock.add_exception(httpx.ReadTimeout("timed out"), url=URL)

        with pytest.raises(DecisionError, match="Could not reach Jev"):
            _ask_backend()

    def test_the_request_text_is_the_jev_request(self):
        text = JevBackend(JevClient(api_key="k")).request_text({"x": float("inf")}, NEUTRAL_QUESTIONS)

        assert json.loads(text) == {
            "state": {"x": None},
            "questions": {
                "route": {"type": "choice", "instructions": "Pick.", "criteria": NEUTRAL_QUESTIONS["route"].options}
            },
        }


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        pytest.param(
            decision_backend.ScoreQuestion(levels=["Calm", "Angry"], instructions="How frustrated?"),
            {"type": "score", "instructions": "How frustrated?", "criteria": ["Calm", "Angry"]},
            id="score",
        ),
        pytest.param(
            decision_backend.YesNoQuestion(instructions="Wants a refund.", true_description="Asks"),
            {"type": "noul", "instructions": "Wants a refund.", "criteria": {"true": "Asks", "false": None}},
            id="yes-no",
        ),
        pytest.param(
            decision_backend.YesNoQuestion(instructions="Wants a refund."),
            {"type": "noul", "instructions": "Wants a refund.", "criteria": None},
            id="yes-no-without-descriptions",
        ),
    ],
)
def test_the_backend_turns_score_and_yes_no_questions_into_jev_questions(question, expected):
    request = JevBackend(JevClient(api_key="k")).request_text({}, {"q": question})

    assert json.loads(request)["questions"] == {"q": expected}


def test_the_backend_reads_each_answer_by_its_questions_type(httpx_mock):
    answers = {
        "frustration": {"type": "score", "score": 1.0, "confidence": 0.6, "probabilities": {"0": 0.4, "1": 0.6}},
        "refund": {"type": "noul", "noul": 0.25},
        "route": {"type": "noul", "noul": 0.5},
    }
    httpx_mock.add_response(url=URL, json={**ANSWER, "answers": answers})
    questions = {
        "frustration": decision_backend.ScoreQuestion(levels=["Calm", "Angry"]),
        "refund": decision_backend.YesNoQuestion(instructions="Wants a refund."),
        "route": NEUTRAL_QUESTIONS["route"],
    }

    response = JevBackend(JevClient(api_key="k")).ask("jev-latest", {}, questions)

    assert response.answers == {
        "frustration": ScoreAnswer(1.0, 0.6, {"0": 0.4, "1": 0.6}),
        "refund": YesNoAnswer(0.25),
    }


def test_the_backend_carries_jevs_request_limits():
    assert JevBackend.limits == JEV_LIMITS
    assert (JEV_LIMITS.max_options, JEV_LIMITS.min_levels, JEV_LIMITS.max_levels) == (255, 1, 10)
    assert JEV_LIMITS.max_questions is None
