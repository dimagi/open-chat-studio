import pytest
from langchain_core.messages import HumanMessage

from apps.service_providers.llm_service import AnthropicLlmService, AzureLlmService, OpenAILlmService
from apps.service_providers.models import LlmProviderTypes


def test_open_ai_service():
    assert LlmProviderTypes.openai.supports_transcription
    assert LlmProviderTypes.openai.supports_assistants
    service = LlmProviderTypes.openai.get_llm_service({"openai_api_key": "test"})
    assert service.supports_transcription
    assert service.supports_assistants


def test_azure_ai_service():
    service = AzureLlmService(
        openai_api_key="test", openai_api_base="https://api.openai.com/v1", openai_api_version="v1"
    )
    assert not service.supports_transcription


def test_anthropic_service():
    service = AnthropicLlmService(anthropic_api_key="test", anthropic_api_base="https://api.anthropic.com")
    assert not service.supports_transcription


@pytest.mark.parametrize(
    "model", ["gpt-3.5-turbo", "gpt-4", "gpt-4o", "gpt-4o-mini-2024-07-18", "ft:gpt-4o-mini-2024-07-18:abc::xyz"]
)
def test_openai_service_get_num_tokens(model):
    service = OpenAILlmService(openai_api_key="123")
    # fine-tuned model
    llm = service.get_chat_model(model, temperature=0.5)
    assert llm.get_num_tokens_from_messages([HumanMessage("Hello")]) > 0
