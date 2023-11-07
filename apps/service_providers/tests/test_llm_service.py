from apps.service_providers.llm_service import AzureLlmService, OpenAILlmService


def test_open_ai_service():
    service = OpenAILlmService(openai_api_key="test")
    assert service.supports_transcription


def test_azure_ai_service():
    service = AzureLlmService(
        openai_api_key="test", openai_api_base="https://api.openai.com/v1", openai_api_version="v1"
    )
    assert not service.supports_transcription
