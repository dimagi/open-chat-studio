import json
from unittest import mock

import httpx
import pytest

from apps.service_providers.llm_service import GoogleLlmService, GoogleVertexAILlmService
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
from apps.service_providers.llm_service.google_decisions import (
    GOOGLE_DECISION_LIMITS,
    GOOGLE_DECISIONS_URL,
    GoogleDecisionBackend,
)

URL = "https://generativelanguage.googleapis.com/v1beta/decisions"

INTENT = ChoiceQuestion(options={"cancel": "Wants to cancel", "other": None}, instructions="What do they want?")
URGENT = YesNoQuestion(instructions="Is this a medical emergency?")
SEVERITY = ScoreQuestion(levels=["well", "mildly unwell", "very unwell"], instructions="How unwell?")

RESPONSE = {
    "answers": {
        "intent": {
            "type": "choice",
            "choice": "cancel",
            "confidence": 0.994,
            "probabilities": {"cancel": 0.994, "other": 0.006},
        },
        "urgent": {"type": "noul", "noul": 0.181, "confidence": 0.819},
        "severity": {
            "type": "score",
            "score": 1.15,
            "confidence": 0.675,
            "probabilities": {"0": 0.087, "1": 0.675, "2": 0.238},
            "legend": {"0": "well", "1": "mildly unwell", "2": "very unwell"},
        },
    },
    "usage": {
        "total_tokens": 215,
        "total_input_tokens": 215,
        "input_tokens_by_modality": [{"modality": "text", "tokens": 215}],
        "total_output_tokens": 0,
    },
}


@pytest.fixture(autouse=True)
def no_sleep():
    with mock.patch("apps.service_providers.llm_service.decision_api.time.sleep") as sleep:
        yield sleep


def _backend() -> GoogleDecisionBackend:
    return GoogleDecisionBackend(api_key="test-key")


def _ask(questions=None, input=None):
    questions = questions or {"intent": INTENT, "urgent": URGENT, "severity": SEVERITY}
    return _backend().ask("gemini-decision-1", input or {"input": "I want to cancel"}, questions)


def _sent(httpx_mock) -> dict:
    return json.loads(httpx_mock.get_request().content)


class TestRequest:
    def test_sends_the_model_input_and_questions(self, httpx_mock):
        httpx_mock.add_response(url=URL, method="POST", json=RESPONSE)

        _ask()

        request = httpx_mock.get_request()
        assert request.headers["x-goog-api-key"] == "test-key"
        assert _sent(httpx_mock) == {
            "model": "gemini-decision-1",
            "input": [{"type": "text", "text": '{"input": "I want to cancel"}'}],
            "questions": {
                "intent": {
                    "type": "choice",
                    "instructions": "What do they want?",
                    "criteria": {"cancel": "Wants to cancel", "other": ""},
                },
                "urgent": {"type": "noul", "instructions": "Is this a medical emergency?"},
                "severity": {
                    "type": "score",
                    "instructions": "How unwell?",
                    "criteria": ["well", "mildly unwell", "very unwell"],
                },
            },
        }

    @pytest.mark.parametrize(
        ("question", "expected"),
        [
            pytest.param(
                YesNoQuestion(instructions="Wants a refund.", true_description="Asks", false_description="Does not"),
                {
                    "type": "noul",
                    "instructions": "Wants a refund.",
                    "criteria": {"true_description": "Asks", "false_description": "Does not"},
                },
                id="yes-no-with-both-descriptions",
            ),
            pytest.param(
                YesNoQuestion(instructions="Wants a refund.", false_description="Does not"),
                {"type": "noul", "instructions": "Wants a refund.", "criteria": {"false_description": "Does not"}},
                id="yes-no-with-one-description",
            ),
            pytest.param(
                ChoiceQuestion(options={"a": None, "b": None}, instructions="Pick."),
                {"type": "choice", "instructions": "Pick.", "criteria": {"a": "", "b": ""}},
                id="choice-without-descriptions",
            ),
        ],
    )
    def test_question_shapes(self, httpx_mock, question, expected):
        httpx_mock.add_response(url=URL, json=RESPONSE)

        _ask({"q": question})

        assert _sent(httpx_mock)["questions"] == {"q": expected}

    def test_the_input_is_one_json_text_block(self, httpx_mock):
        httpx_mock.add_response(url=URL, json=RESPONSE)
        state = {
            "input": "Je veux annuler",
            "history": [{"role": "human", "content": "Bonjour"}],
            "participant_data": {"score": float("nan")},
        }

        _ask(input=state)

        (block,) = _sent(httpx_mock)["input"]
        assert block["type"] == "text"
        assert "Je veux annuler" in block["text"]
        assert json.loads(block["text"]) == {**state, "participant_data": {"score": None}}

    def test_the_request_text_is_the_google_request(self):
        text = _backend().request_text({"input": "hi"}, {"urgent": URGENT})

        assert json.loads(text) == {
            "input": [{"type": "text", "text": '{"input": "hi"}'}],
            "questions": {"urgent": {"type": "noul", "instructions": "Is this a medical emergency?"}},
        }


class TestResponse:
    def test_reads_every_answer_and_the_usage(self, httpx_mock):
        httpx_mock.add_response(url=URL, json=RESPONSE)

        response = _ask()

        assert response.answers == {
            "intent": ChoiceAnswer("cancel", 0.994, {"cancel": 0.994, "other": 0.006}),
            "urgent": YesNoAnswer(0.181, confidence=0.819),
            "severity": ScoreAnswer(1.15, 0.675, {"0": 0.087, "1": 0.675, "2": 0.238}),
        }
        assert response.usage == DecisionUsage(input_tokens=215, output_tokens=0)
        assert response.raw_answers == RESPONSE["answers"]

    def test_the_model_is_the_one_requested(self, httpx_mock):
        httpx_mock.add_response(url=URL, json=RESPONSE)

        assert _ask().model == "gemini-decision-1"

    @pytest.mark.parametrize(
        ("name", "answer"),
        [
            pytest.param("intent", None, id="missing"),
            pytest.param("intent", {"type": "noul", "noul": 0.5, "confidence": 0.5}, id="other-type"),
            pytest.param("intent", {"type": "choice", "choice": "cancel"}, id="choice-without-confidence"),
            pytest.param(
                "intent", {"type": "choice", "choice": "cancel", "confidence": 1.2}, id="confidence-above-one"
            ),
            pytest.param("severity", {"type": "score", "score": "Infinity", "confidence": 0.5}, id="infinite-score"),
            pytest.param("urgent", {"type": "noul", "confidence": 0.5}, id="yes-no-without-probability"),
            pytest.param("urgent", {"type": "noul", "noul": 0.5, "confidence": "high"}, id="bad-yes-no-confidence"),
        ],
    )
    def test_a_missing_or_malformed_answer_is_left_out(self, httpx_mock, name, answer):
        answers = {key: value for key, value in RESPONSE["answers"].items() if key != name}
        if answer is not None:
            answers[name] = answer
        httpx_mock.add_response(url=URL, json={**RESPONSE, "answers": answers})

        response = _ask()

        assert name not in response.answers
        assert len(response.answers) == 2

    def test_a_yes_no_answer_without_confidence_is_read(self, httpx_mock):
        httpx_mock.add_response(url=URL, json={**RESPONSE, "answers": {"urgent": {"type": "noul", "noul": 0.3}}})

        assert _ask({"urgent": URGENT}).yes_no("urgent") == YesNoAnswer(0.3)

    def test_missing_usage_reads_as_zero(self, httpx_mock):
        httpx_mock.add_response(url=URL, json={"answers": RESPONSE["answers"]})

        assert _ask().usage == DecisionUsage(0, 0)

    @pytest.mark.parametrize(
        "usage",
        [
            pytest.param({"total_input_tokens": -1}, id="negative"),
            pytest.param({"total_input_tokens": "215"}, id="string"),
            pytest.param({"total_input_tokens": 215, "total_output_tokens": True}, id="boolean"),
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
            pytest.param(b"[]", id="not-an-object"),
        ],
    )
    def test_an_unreadable_body_is_a_decision_error(self, httpx_mock, content):
        httpx_mock.add_response(url=URL, content=content)

        with pytest.raises(DecisionError, match="could not be read"):
            _ask()


def _google_error(code, message, status=None, reason=None) -> dict:
    """Decision errors carry a string code; the API gateway's errors a numeric one, a status and details."""
    error = {"code": code, "message": message}
    if status:
        error["status"] = status
    if reason:
        error["details"] = [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": reason}]
    return {"error": error}


class TestErrors:
    @pytest.mark.parametrize(
        ("status_code", "body"),
        [
            pytest.param(401, _google_error(401, "Unauthenticated", "UNAUTHENTICATED"), id="unauthorized"),
            pytest.param(403, _google_error(403, "Permission denied", "PERMISSION_DENIED"), id="forbidden"),
            pytest.param(
                400,
                _google_error(400, "API key not valid.", "INVALID_ARGUMENT", reason="API_KEY_INVALID"),
                id="invalid-api-key",
            ),
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
                _google_error("safety", "The input was blocked."),
                "Google returned 400 (safety): The input was blocked.",
                id="safety",
            ),
            pytest.param(
                400,
                _google_error("prohibited_content", "Blocked."),
                "Google returned 400 (prohibited_content): Blocked.",
                id="prohibited-content",
            ),
            pytest.param(
                400,
                _google_error("invalid_request", "must have between 2 and 255 options"),
                "Google returned 400 (invalid_request): must have between 2 and 255 options",
                id="validation",
            ),
            pytest.param(
                404,
                _google_error("not_found", "Requested model not found."),
                "Google returned 404 (not_found): Requested model not found.",
                id="unknown-model",
            ),
            pytest.param(
                400,
                _google_error(400, "Bad JSON", "INVALID_ARGUMENT"),
                "Google returned 400 (INVALID_ARGUMENT): Bad JSON",
                id="gateway-error",
            ),
            pytest.param(500, None, "Google returned 500", id="server-error-without-a-body"),
        ],
    )
    def test_other_failures_are_decision_errors_naming_the_code(self, httpx_mock, status_code, body, message):
        httpx_mock.add_response(url=URL, status_code=status_code, json=body)

        with pytest.raises(DecisionError) as excinfo:
            _ask()

        assert type(excinfo.value) is DecisionError
        assert excinfo.value.status_code == status_code
        assert str(excinfo.value).startswith(message)

    def test_a_network_failure_is_a_decision_error(self, httpx_mock):
        httpx_mock.add_exception(httpx.ReadTimeout("timed out"), url=URL)

        with pytest.raises(DecisionError, match="Could not reach Google: ReadTimeout"):
            _ask()


class TestRetries:
    @pytest.mark.parametrize("status_code", [pytest.param(429, id="rate-limited"), pytest.param(503, id="unavailable")])
    def test_retries_once(self, httpx_mock, no_sleep, status_code):
        httpx_mock.add_response(url=URL, status_code=status_code, headers={"retry-after": "2"})
        httpx_mock.add_response(url=URL, json=RESPONSE)

        assert _ask().choice("intent").choice == "cancel"
        no_sleep.assert_called_once_with(2.0)

    def test_the_wait_is_bounded(self, httpx_mock, no_sleep):
        httpx_mock.add_response(url=URL, status_code=429, headers={"retry-after": "60"})
        httpx_mock.add_response(url=URL, json=RESPONSE)

        _ask()

        no_sleep.assert_called_once_with(5.0)

    def test_gives_up_after_one_retry(self, httpx_mock):
        error = _google_error(503, "The model is overloaded.", "UNAVAILABLE")
        httpx_mock.add_response(url=URL, status_code=503, json=error)
        httpx_mock.add_response(url=URL, status_code=503, json=error)

        with pytest.raises(DecisionError, match="Google returned 503") as excinfo:
            _ask()

        assert excinfo.value.status_code == 503
        assert len(httpx_mock.get_requests()) == 2

    def test_a_validation_error_is_not_retried(self, httpx_mock):
        httpx_mock.add_response(url=URL, status_code=400, json=_google_error("invalid_request", "bad"))

        with pytest.raises(DecisionError):
            _ask()

        assert len(httpx_mock.get_requests()) == 1


def test_the_limits_follow_googles_guide():
    limits = GoogleDecisionBackend.limits

    assert limits == GOOGLE_DECISION_LIMITS
    assert (limits.min_options, limits.max_options) == (2, 255)
    assert (limits.min_levels, limits.max_levels) == (2, 255)
    assert limits.max_questions == 1000
    assert limits.max_option_length == 128
    assert limits.requires_instructions is True


@pytest.mark.parametrize(
    ("key", "accepted"),
    [
        pytest.param("intent", True, id="identifier"),
        pytest.param("wants-refund_2", True, id="hyphen"),
        pytest.param("a" * 64, True, id="longest"),
        pytest.param("a" * 65, False, id="too-long"),
        pytest.param("intención", False, id="not-ascii"),
    ],
)
def test_the_key_rule_follows_googles_guide(key, accepted):
    assert GOOGLE_DECISION_LIMITS.accepts_key(key) is accepted


def test_the_google_service_returns_a_google_backend():
    backend = GoogleLlmService(google_api_key="test-key").get_decision_backend()

    assert isinstance(backend, GoogleDecisionBackend)
    assert backend.provider_type == "google"
    assert backend.api_key == "test-key"
    assert backend.url == GOOGLE_DECISIONS_URL == URL


def test_vertex_ai_has_no_decision_backend():
    service = GoogleVertexAILlmService(credentials_json={"project_id": "p"})

    assert service.get_decision_backend() is None
