import pytest

from apps.service_providers.llm_service import AnthropicLlmService, JevLlmService
from apps.service_providers.llm_service.decision_backend import (
    ChoiceAnswer,
    DecisionAuthenticationError,
    DecisionError,
    DecisionResponse,
    DecisionUsage,
    ScoreAnswer,
    YesNoAnswer,
)
from apps.service_providers.llm_service.jev import JevBackend

CHOICE = ChoiceAnswer("BILLING", 0.8, {"BILLING": 0.8, "SUPPORT": 0.2})
SCORE = ScoreAnswer(1.0, 0.7, {"0": 0.3, "1": 0.7})
YES_NO = YesNoAnswer(0.9)
ANSWERS = {"route": CHOICE, "frustration": SCORE, "refund": YES_NO}


def _response(answers) -> DecisionResponse:
    return DecisionResponse("model-1", answers, DecisionUsage(10, 0), raw_answers={})


@pytest.mark.parametrize(
    ("answers", "expected"),
    [
        pytest.param({"route": CHOICE}, CHOICE, id="choice"),
        pytest.param({}, None, id="missing"),
        pytest.param({"other": CHOICE}, None, id="other-name"),
    ],
)
def test_choice_reads_the_named_choice_answer(answers, expected):
    assert _response(answers).choice("route") == expected


@pytest.mark.parametrize(
    ("read", "name", "expected"),
    [
        pytest.param(DecisionResponse.choice, "route", CHOICE, id="choice"),
        pytest.param(DecisionResponse.score, "frustration", SCORE, id="score"),
        pytest.param(DecisionResponse.yes_no, "refund", YES_NO, id="yes-no"),
        pytest.param(DecisionResponse.score, "route", None, id="choice-read-as-score"),
        pytest.param(DecisionResponse.yes_no, "frustration", None, id="score-read-as-yes-no"),
        pytest.param(DecisionResponse.choice, "refund", None, id="yes-no-read-as-choice"),
    ],
)
def test_each_accessor_reads_only_its_own_answer_type(read, name, expected):
    assert read(_response(ANSWERS), name) == expected


def test_a_yes_no_answer_may_have_no_confidence():
    assert YesNoAnswer(0.4).confidence is None


def test_an_authentication_error_is_a_decision_error():
    error = DecisionAuthenticationError("rejected", status_code=401)

    assert isinstance(error, DecisionError)
    assert error.status_code == 401


@pytest.mark.parametrize(
    "service",
    [
        pytest.param(AnthropicLlmService(anthropic_api_key="k", anthropic_api_base="https://x"), id="anthropic"),
    ],
)
def test_a_chat_provider_has_no_decision_backend(service):
    assert service.get_decision_backend() is None


def test_the_jev_service_returns_a_jev_backend():
    backend = JevLlmService(jev_api_key="test", jev_api_base="http://localhost:9999").get_decision_backend()

    assert isinstance(backend, JevBackend)
    assert backend.provider_type == "jev"
    assert backend.client.url == "http://localhost:9999/v1/systemone"
