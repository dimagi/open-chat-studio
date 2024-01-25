from apps.service_providers.llm_service import AnthropicLlmService, AzureLlmService, OpenAILlmService


def test_open_ai_service():
    service = OpenAILlmService(openai_api_key="test")
    assert service.supports_transcription


def test_azure_ai_service():
    service = AzureLlmService(
        openai_api_key="test", openai_api_base="https://api.openai.com/v1", openai_api_version="v1"
    )
    assert not service.supports_transcription


def test_anthropic_service():
    service = AnthropicLlmService(anthropic_api_key="test", anthropic_api_base="https://api.anthropic.com")
    assert not service.supports_transcription
