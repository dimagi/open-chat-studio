import json
from unittest import mock

import httpx
import pytest

from apps.service_providers.llm_service import AzureLlmService, OpenAIGenericService, OpenAILlmService
from apps.service_providers.llm_service.decision_backend import (
    ChoiceAnswer,
    ChoiceQuestion,
    DecisionAuthenticationError,
    DecisionError,
    DecisionUsage,
    ScoreAnswer,
    ScoreQuestion,
    YesNoAnswer,
    YesNoQuestion,
)
from apps.service_providers.llm_service.openai_decisions import (
    OPENAI_DECISION_LIMITS,
    OpenAIDecisionBackend,
)

URL = "https://api.openai.com/v1/decisions"

INTENT = ChoiceQuestion(options={"cancel": "Wants to cancel", "other": None}, instructions="What do they want?")
URGENT = YesNoQuestion(instructions="Is this a medical emergency?")
SEVERITY = ScoreQuestion(levels=["well", "mildly unwell", "very unwell"], instructions="How unwell?")

INTENT_ANSWER = {
    "name": "intent",
    "type": "choice",
    "choice": "cancel",
    "confidence": 0.994,
    "probabilities": [{"value": "cancel", "probability": 0.994}, {"value": "other", "probability": 0.006}],
}
URGENT_ANSWER = {"name": "urgent", "type": "predicate", "probability": 0.181}
SEVERITY_ANSWER = {
    "name": "severity",
    "type": "score",
    "score": 1.15,
    "confidence": 0.675,
    "probabilities": [
        {"value": 0, "label": "well", "probability": 0.087},
        {"value": 1, "label": "mildly unwell", "probability": 0.675},
        {"value": 2, "label": "very unwell", "probability": 0.238},
    ],
}

RESPONSE = {
    "model": "gpt-6-luna",
    "answers": [INTENT_ANSWER, URGENT_ANSWER, SEVERITY_ANSWER],
    "usage": {
        "input_tokens": 215,
        "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
        "output_tokens": 0,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": 215,
    },
}


@pytest.fixture(autouse=True)
def no_sleep():
    with mock.patch("apps.service_providers.llm_service.decision_api.time.sleep") as sleep:
        yield sleep


def _backend(**kwargs) -> OpenAIDecisionBackend:
    return OpenAIDecisionBackend(api_key="test-key", **kwargs)


def _ask(questions=None, input=None, backend=None):
    questions = questions or {"intent": INTENT, "urgent": URGENT, "severity": SEVERITY}
    return (backend or _backend()).ask("gpt-6-luna", input or {"input": "I want to cancel"}, questions)


def _sent(httpx_mock) -> dict:
    return json.loads(httpx_mock.get_request().content)


class TestRequest:
    def test_sends_the_model_input_and_questions(self, httpx_mock):
        httpx_mock.add_response(url=URL, method="POST", json=RESPONSE)

        _ask()

        request = httpx_mock.get_request()
        assert request.headers["authorization"] == "Bearer test-key"
        assert "openai-organization" not in request.headers
        assert _sent(httpx_mock) == {
            "model": "gpt-6-luna",
            "input": '{"input": "I want to cancel"}',
            "questions": [
                {
                    "type": "choice",
                    "name": "intent",
                    "instructions": "What do they want?",
                    "choices": [{"value": "cancel", "description": "Wants to cancel"}, {"value": "other"}],
                },
                {"type": "predicate", "name": "urgent", "instructions": "Is this a medical emergency?"},
                {
                    "type": "score",
                    "name": "severity",
                    "instructions": "How unwell?",
                    "levels": [{"label": "well"}, {"label": "mildly unwell"}, {"label": "very unwell"}],
                },
            ],
        }

    @pytest.mark.parametrize(
        ("question", "instructions"),
        [
            pytest.param(
                YesNoQuestion(instructions="Wants a refund.", true_description="Asks", false_description="Does not"),
                "Wants a refund.\n\nTrue when: Asks\nFalse when: Does not",
                id="both-descriptions",
            ),
            pytest.param(
                YesNoQuestion(instructions="Wants a refund.", false_description="Does not"),
                "Wants a refund.\n\nFalse when: Does not",
                id="one-description",
            ),
            pytest.param(YesNoQuestion(true_description="Asks"), "True when: Asks", id="no-instructions"),
        ],
    )
    def test_yes_no_descriptions_are_part_of_the_instructions(self, httpx_mock, question, instructions):
        httpx_mock.add_response(url=URL, json=RESPONSE)

        _ask({"q": question})

        assert _sent(httpx_mock)["questions"] == [{"type": "predicate", "name": "q", "instructions": instructions}]

    def test_the_input_is_the_states_json_text(self, httpx_mock):
        httpx_mock.add_response(url=URL, json=RESPONSE)
        state = {
            "input": "Je veux annuler",
            "history": [{"role": "human", "content": "Bonjour"}],
            "participant_data": {"score": float("nan")},
        }

        _ask(input=state)

        text = _sent(httpx_mock)["input"]
        assert "Je veux annuler" in text
        assert json.loads(text) == {**state, "participant_data": {"score": None}}

    def test_sends_the_organization_to_a_custom_base_url(self, httpx_mock):
        url = "https://proxy.example.com/v1/decisions"
        httpx_mock.add_response(url=url, json=RESPONSE)

        _ask(backend=_backend(base_url="https://proxy.example.com/v1/", organization="org-1"))

        assert httpx_mock.get_request().headers["openai-organization"] == "org-1"

    def test_the_request_text_is_the_openai_request(self):
        text = _backend().request_text({"input": "hi"}, {"urgent": URGENT})

        assert json.loads(text) == {
            "input": '{"input": "hi"}',
            "questions": [{"type": "predicate", "name": "urgent", "instructions": "Is this a medical emergency?"}],
        }


class TestResponse:
    def test_reads_every_answer_and_the_usage(self, httpx_mock):
        httpx_mock.add_response(url=URL, json=RESPONSE)

        response = _ask()

        assert response.answers == {
            "intent": ChoiceAnswer("cancel", 0.994, {"cancel": 0.994, "other": 0.006}),
            "urgent": YesNoAnswer(0.181),
            "severity": ScoreAnswer(1.15, 0.675, {"0": 0.087, "1": 0.675, "2": 0.238}),
        }
        assert response.usage == DecisionUsage(input_tokens=215, output_tokens=0)
        assert response.raw_answers == {"intent": INTENT_ANSWER, "urgent": URGENT_ANSWER, "severity": SEVERITY_ANSWER}

    def test_the_model_is_the_one_requested(self, httpx_mock):
        httpx_mock.add_response(url=URL, json={**RESPONSE, "model": "gpt-6-luna-2026-10-01"})

        assert _ask().model == "gpt-6-luna"

    def test_answers_are_matched_by_name_not_position(self, httpx_mock):
        reordered = [SEVERITY_ANSWER, INTENT_ANSWER, URGENT_ANSWER]
        httpx_mock.add_response(url=URL, json={**RESPONSE, "answers": reordered})

        response = _ask()

        assert response.choice("intent").choice == "cancel"
        assert response.score("severity").score == 1.15

    def test_an_unnamed_answer_is_matched_by_position(self, httpx_mock):
        unnamed = [{**answer, "name": None} for answer in RESPONSE["answers"]]
        httpx_mock.add_response(url=URL, json={**RESPONSE, "answers": unnamed})

        response = _ask()

        assert response.choice("intent").choice == "cancel"
        assert response.yes_no("urgent") == YesNoAnswer(0.181)

    def test_a_refused_question_has_no_answer_and_the_others_are_read(self, httpx_mock):
        refusal = {"name": "urgent", "type": "refusal"}
        httpx_mock.add_response(url=URL, json={**RESPONSE, "answers": [INTENT_ANSWER, refusal, SEVERITY_ANSWER]})

        response = _ask()

        assert set(response.answers) == {"intent", "severity"}
        assert response.raw_answers["urgent"] == refusal

    @pytest.mark.parametrize(
        ("name", "answer"),
        [
            pytest.param("intent", None, id="missing"),
            pytest.param("intent", {"name": "intent", "type": "predicate", "probability": 0.5}, id="other-type"),
            pytest.param("intent", {"name": "intent", "type": "choice", "choice": "cancel"}, id="no-confidence"),
            pytest.param(
                "intent",
                {**INTENT_ANSWER, "probabilities": {"cancel": 0.994}},
                id="probabilities-not-a-list",
            ),
            pytest.param("severity", {**SEVERITY_ANSWER, "score": "Infinity"}, id="infinite-score"),
            pytest.param("urgent", {"name": "urgent", "type": "predicate"}, id="yes-no-without-probability"),
            pytest.param(
                "urgent", {"name": "urgent", "type": "predicate", "probability": 1.5}, id="probability-above-one"
            ),
        ],
    )
    def test_a_missing_or_malformed_answer_is_left_out(self, httpx_mock, name, answer):
        answers = [value for value in RESPONSE["answers"] if value["name"] != name]
        if answer is not None:
            answers.append(answer)
        httpx_mock.add_response(url=URL, json={**RESPONSE, "answers": answers})

        response = _ask()

        assert name not in response.answers
        assert len(response.answers) == 2

    def test_missing_usage_reads_as_zero(self, httpx_mock):
        httpx_mock.add_response(url=URL, json={"answers": RESPONSE["answers"]})

        assert _ask().usage == DecisionUsage(0, 0)

    @pytest.mark.parametrize(
        "usage",
        [
            pytest.param({"input_tokens": -1}, id="negative"),
            pytest.param({"input_tokens": "215"}, id="string"),
            pytest.param({"input_tokens": 215, "output_tokens": True}, id="boolean"),
        ],
    )
    def test_a_malformed_token_count_is_a_decision_error(self, httpx_mock, usage):
        httpx_mock.add_response(url=URL, json={**RESPONSE, "usage": usage})

        with pytest.raises(DecisionError, match="could not be read"):
            _ask()

    @pytest.mark.parametrize(
        "content",
        [
            pytest.param(b"<html>gateway</html>", id="not-json"),
            pytest.param(b'{"usage": {}}', id="no-answers"),
            pytest.param(b'{"answers": {"intent": {}}}', id="answers-not-a-list"),
            pytest.param(b"[]", id="not-an-object"),
        ],
    )
    def test_an_unreadable_body_is_a_decision_error(self, httpx_mock, content):
        httpx_mock.add_response(url=URL, content=content)

        with pytest.raises(DecisionError, match="could not be read"):
            _ask()


def _openai_error(message, type_, code=None) -> dict:
    return {"error": {"message": message, "type": type_, "param": None, "code": code}}


class TestErrors:
    @pytest.mark.parametrize(
        ("status_code", "body"),
        [
            pytest.param(
                401, _openai_error("Incorrect API key provided", "invalid_request_error", "invalid_api_key"), id="key"
            ),
            pytest.param(403, _openai_error("Project does not have access", "invalid_request_error"), id="forbidden"),
        ],
    )
    def test_a_rejected_key_is_an_authentication_error(self, httpx_mock, status_code, body):
        httpx_mock.add_response(url=URL, status_code=status_code, json=body)

        with pytest.raises(DecisionAuthenticationError) as excinfo:
            _ask()

        assert excinfo.value.status_code == status_code

    @pytest.mark.parametrize(
        ("status_code", "body", "message"),
        [
            pytest.param(
                400,
                _openai_error("choices must have at least 2 items", "invalid_request_error", "invalid_value"),
                "OpenAI returned 400 (invalid_value): choices must have at least 2 items",
                id="validation",
            ),
            pytest.param(
                404,
                _openai_error("The model does not exist", "invalid_request_error", "model_not_found"),
                "OpenAI returned 404 (model_not_found): The model does not exist",
                id="unknown-model",
            ),
            pytest.param(
                400,
                _openai_error("Bad request", "invalid_request_error"),
                "OpenAI returned 400 (invalid_request_error): Bad request",
                id="type-without-a-code",
            ),
            pytest.param(500, None, "OpenAI returned 500", id="server-error-without-a-body"),
        ],
    )
    def test_other_failures_are_decision_errors_naming_the_code(self, httpx_mock, status_code, body, message):
        httpx_mock.add_response(url=URL, status_code=status_code, json=body)
        if status_code == 500:
            httpx_mock.add_response(url=URL, status_code=status_code, json=body)

        with pytest.raises(DecisionError) as excinfo:
            _ask()

        assert type(excinfo.value) is DecisionError
        assert excinfo.value.status_code == status_code
        assert str(excinfo.value).startswith(message)

    def test_a_network_failure_is_a_decision_error(self, httpx_mock):
        httpx_mock.add_exception(httpx.ReadTimeout("timed out"), url=URL)

        with pytest.raises(DecisionError, match="Could not reach OpenAI: ReadTimeout"):
            _ask()


class TestRetries:
    @pytest.mark.parametrize(
        "status_code",
        [
            pytest.param(429, id="rate-limited"),
            pytest.param(500, id="server-error"),
            pytest.param(503, id="overloaded"),
        ],
    )
    def test_retries_once(self, httpx_mock, no_sleep, status_code):
        httpx_mock.add_response(url=URL, status_code=status_code, headers={"retry-after": "2"})
        httpx_mock.add_response(url=URL, json=RESPONSE)

        assert _ask().choice("intent").choice == "cancel"
        no_sleep.assert_called_once_with(2.0)

    def test_gives_up_after_one_retry(self, httpx_mock):
        error = _openai_error("Rate limit reached", "requests", "rate_limit_exceeded")
        httpx_mock.add_response(url=URL, status_code=429, json=error)
        httpx_mock.add_response(url=URL, status_code=429, json=error)

        with pytest.raises(DecisionError, match="OpenAI returned 429") as excinfo:
            _ask()

        assert excinfo.value.status_code == 429
        assert len(httpx_mock.get_requests()) == 2

    def test_a_validation_error_is_not_retried(self, httpx_mock):
        httpx_mock.add_response(url=URL, status_code=400, json=_openai_error("bad", "invalid_request_error"))

        with pytest.raises(DecisionError):
            _ask()

        assert len(httpx_mock.get_requests()) == 1


def test_the_limits_follow_the_openai_reference():
    limits = OpenAIDecisionBackend.limits

    assert limits == OPENAI_DECISION_LIMITS
    assert (limits.min_options, limits.max_options) == (2, 255)
    assert (limits.min_levels, limits.max_levels) == (2, 10)
    assert limits.max_questions == 400
    assert OpenAIDecisionBackend.reports_yes_no_confidence is False


def test_the_openai_service_returns_an_openai_backend():
    service = OpenAILlmService(
        openai_api_key="test-key", openai_api_base="https://proxy.example.com/v1", openai_organization="org-1"
    )

    backend = service.get_decision_backend()

    assert isinstance(backend, OpenAIDecisionBackend)
    assert backend.provider_type == "openai"
    assert backend.api_key == "test-key"
    assert backend.url == "https://proxy.example.com/v1/decisions"
    assert backend.organization == "org-1"


def test_the_openai_service_defaults_to_the_openai_api():
    assert OpenAILlmService(openai_api_key="test-key").get_decision_backend().url == URL


@pytest.mark.parametrize(
    "service",
    [
        pytest.param(OpenAIGenericService(openai_api_key="k", openai_api_base="https://api.groq.com/v1"), id="generic"),
        pytest.param(
            AzureLlmService(openai_api_key="k", openai_api_base="https://x.azure.com", openai_api_version="v"),
            id="azure",
        ),
    ],
)
def test_other_openai_compatible_services_have_no_decision_backend(service):
    assert service.get_decision_backend() is None
