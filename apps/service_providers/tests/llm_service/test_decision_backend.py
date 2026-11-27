import pytest

from apps.service_providers.llm_service import AnthropicLlmService, JevLlmService, OpenAILlmService
from apps.service_providers.llm_service.decision_backend import (
    ChoiceAnswer,
    DecisionAuthenticationError,
    DecisionError,
    DecisionResponse,
    DecisionUsage,
)
from apps.service_providers.llm_service.jev import JevBackend

CHOICE = ChoiceAnswer("BILLING", 0.8, {"BILLING": 0.8, "SUPPORT": 0.2})


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


def test_an_authentication_error_is_a_decision_error():
    error = DecisionAuthenticationError("rejected", status_code=401)

    assert isinstance(error, DecisionError)
    assert error.status_code == 401


@pytest.mark.parametrize(
    "service",
    [
        pytest.param(OpenAILlmService(openai_api_key="k"), id="openai"),
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
