from unittest.mock import Mock, patch

from anthropic.types import Message, TextBlock, Usage
from openai.types.chat.chat_completion import ChatCompletion, Choice
from openai.types.chat.chat_completion_message import ChatCompletionMessage
from openai.types.completion_usage import CompletionUsage

from apps.accounting.models import UsageType
from apps.accounting.usage import UsageRecord, UsageRecorder
from apps.experiments.models import ExperimentSession
from apps.service_providers.llm_service import AnthropicLlmService, OpenAILlmService
from apps.service_providers.models import LlmProvider

RESPONSE_TEXT = "I'm doing well, thank you for asking."


@patch("openai.resources.chat.completions.Completions.create")
def test_usage_recording_for_openai(mock_create):
    mock_create.return_value = ChatCompletion(
        id="cmpl-123",
        object="chat.completion",
        created=1629352661,
        model="gpt-3.5-turbo",
        choices=[
            Choice(
                finish_reason="stop",
                index=0,
                message=ChatCompletionMessage(
                    content=RESPONSE_TEXT,
                    role="assistant",
                ),
            )
        ],
        usage=CompletionUsage(
            completion_tokens=10,
            prompt_tokens=5,
            total_tokens=15,
        ),
    )

    def service_factory(usage_recorder):
        return OpenAILlmService(openai_api_key="my-api-key", usage_recorder=usage_recorder)

    _test_usage_recording_for_chat_model(service_factory, expected_input_tokens=5, expected_output_tokens=10)


@patch("anthropic.resources.messages.Messages.create")
def test_usage_recording_for_anthropic(mock_create):
    mock_create.return_value = Message(
        id="msg-123",
        content=[TextBlock(text=RESPONSE_TEXT, type="text")],
        model="sonet",
        role="assistant",
        type="message",
        usage=Usage(
            input_tokens=5,
            output_tokens=10,
        ),
    )

    def service_factory(usage_recorder):
        return AnthropicLlmService(
            anthropic_api_key="my-api-key",
            anthropic_api_base="https://api.anthropic.com",
            usage_recorder=usage_recorder,
        )

    _test_usage_recording_for_chat_model(service_factory, expected_input_tokens=5, expected_output_tokens=10)


def _test_usage_recording_for_chat_model(service_factory, expected_input_tokens, expected_output_tokens):
    service_object = LlmProvider(id=1, team_id=123)
    usage_recorder = UsageRecorder(service_object)
    usage_recorder.commit_and_clear = Mock()

    service = service_factory(usage_recorder)
    model = service.get_chat_model("gpt-3", temperature=0.5)

    source_object = ExperimentSession(id=1, team_id=123)
    with service.usage_scope(source_object, metadata={"session_id": "session"}):
        response = model.invoke("Hello, how are you?")

    assert response.content == "I'm doing well, thank you for asking."

    usage = usage_recorder.usage
    assert usage == [
        UsageRecord(
            team_id=123,
            service_object=service_object,
            source_object=source_object,
            type=UsageType.INPUT_TOKENS,
            value=expected_input_tokens,
            metadata={"session_id": "session", "model": "gpt-3"},
        ),
        UsageRecord(
            team_id=123,
            service_object=service_object,
            source_object=source_object,
            type=UsageType.OUTPUT_TOKENS,
            value=expected_output_tokens,
            metadata={"session_id": "session", "model": "gpt-3"},
        ),
    ]
