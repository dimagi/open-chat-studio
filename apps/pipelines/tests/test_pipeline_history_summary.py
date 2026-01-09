from unittest import mock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from apps.chat.conversation import compress_pipeline_chat_history
from apps.pipelines.models import PipelineChatHistoryModes, PipelineChatHistoryTypes, PipelineChatMessages
from apps.utils.factories.experiment import (
    ExperimentSessionFactory,
)
from apps.utils.factories.pipelines import PipelineChatHistoryFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory
from apps.utils.langchain import FakeLlmSimpleTokenCount
from apps.utils.pytest import django_db_with_data


@pytest.fixture()
def provider():
    return LlmProviderFactory()


@pytest.fixture()
def experiment_session():
    return ExperimentSessionFactory()


@pytest.fixture()
def pipeline_chat_history(experiment_session):
    return PipelineChatHistoryFactory(session=experiment_session)


@django_db_with_data()
def test_no_summary_returns_all_messages(experiment_session):
    history = experiment_session.pipeline_chat_history.create(type=PipelineChatHistoryTypes.NAMED, name="name")
    message1 = history.messages.create(ai_message="I am a robot", human_message="hi, please fetch me a coffee")
    message2 = history.messages.create(ai_message="I can't do that", human_message="sudo, please fetch me a coffee")
    expected_messages = [
        HumanMessage(content="hi, please fetch me a coffee", additional_kwargs={"id": message1.id, "node_id": ""}),
        AIMessage(content="I am a robot", additional_kwargs={"id": message1.id, "node_id": ""}),
        HumanMessage(content="sudo, please fetch me a coffee", additional_kwargs={"id": message2.id, "node_id": ""}),
        AIMessage(content="I can't do that", additional_kwargs={"id": message2.id, "node_id": ""}),
    ]
    summary_messages = history.get_langchain_messages_until_marker(PipelineChatHistoryModes.SUMMARIZE)
    assert expected_messages == summary_messages


@django_db_with_data()
def test_get_messages_returns_until_marker(experiment_session):
    history = experiment_session.pipeline_chat_history.create(type=PipelineChatHistoryTypes.NAMED, name="name")
    history.messages.create(ai_message="I am a robot", human_message="hi, please fetch me a coffee")
    message2 = history.messages.create(
        ai_message="I can't do that",
        human_message="sudo, please fetch me a coffee",
        summary="argument",
        compression_marker=PipelineChatHistoryModes.SUMMARIZE,
    )
    message3 = history.messages.create(
        ai_message="I am a robot",
        human_message="how about some tea",
        compression_marker=PipelineChatHistoryModes.TRUNCATE_TOKENS,
    )
    summary_messages = history.get_langchain_messages_until_marker(PipelineChatHistoryModes.SUMMARIZE)
    assert summary_messages == [
        SystemMessage(content="argument", additional_kwargs={"id": message2.id, "node_id": ""}),
        HumanMessage(content="sudo, please fetch me a coffee", additional_kwargs={"id": message2.id, "node_id": ""}),
        AIMessage(content="I can't do that", additional_kwargs={"id": message2.id, "node_id": ""}),
        HumanMessage(content="how about some tea", additional_kwargs={"id": message3.id, "node_id": ""}),
        AIMessage(content="I am a robot", additional_kwargs={"id": message3.id, "node_id": ""}),
    ]

    truncate_messages = history.get_langchain_messages_until_marker(PipelineChatHistoryModes.TRUNCATE_TOKENS)
    assert truncate_messages == [
        HumanMessage(content="how about some tea", additional_kwargs={"id": message3.id, "node_id": ""}),
        AIMessage(content="I am a robot", additional_kwargs={"id": message3.id, "node_id": ""}),
    ]


@django_db_with_data()
def test_compress_history_no_need_for_compression(pipeline_chat_history):
    for i in range(15):
        # (3 tokens for human messages + 3 tokens for ai messages) * 15 messages)
        # = 90 tokens in total
        pipeline_chat_history.messages.create(human_message=f"Hello {i}", ai_message=f"Hello {i}")

    token_limit = 90
    compress_pipeline_chat_history(
        pipeline_chat_history,
        FakeLlmSimpleTokenCount(responses=["Summary"]),
        max_token_limit=token_limit,
        input_messages=[],
        history_mode=PipelineChatHistoryModes.SUMMARIZE,
    )
    messages = pipeline_chat_history.get_langchain_messages_until_marker(PipelineChatHistoryModes.SUMMARIZE)
    # No summary messages
    assert not any(isinstance(message, SystemMessage) for message in messages)


@django_db_with_data()
@mock.patch("apps.chat.conversation._get_new_summary")
def test_create_summary_token_limit_reached(mock_get_new_summary, pipeline_chat_history):
    summary_message = "Summary"  # 2 tokens
    mock_get_new_summary.return_value = summary_message

    for i in range(15):
        # (3 tokens for human messages + 3 tokens for ai messages) * 15 messages)
        # = 90 tokens in total
        pipeline_chat_history.messages.create(human_message=f"Hello {i}", ai_message=f"Hello {i}")

    token_limit = 80
    compressed_history = compress_pipeline_chat_history(
        pipeline_chat_history,
        FakeLlmSimpleTokenCount(responses=["Summary"]),
        max_token_limit=token_limit,
        input_messages=[],
    )
    assert isinstance(compressed_history[0], SystemMessage)
    assert compressed_history[0].content == summary_message
    assert PipelineChatMessages.objects.get(id=compressed_history[1].additional_kwargs["id"]).summary == "Summary"

    summary_messages = pipeline_chat_history.get_langchain_messages_until_marker(PipelineChatHistoryModes.SUMMARIZE)
    assert isinstance(summary_messages[0], SystemMessage)
    assert isinstance(summary_messages[1], HumanMessage)
    assert isinstance(summary_messages[2], AIMessage)


@pytest.mark.django_db()
def test_max_history_length_compression(pipeline_chat_history):
    for i in range(4):
        pipeline_chat_history.messages.create(human_message=f"Hello {i}", ai_message=f"Hi {i}")

    llm = FakeLlmSimpleTokenCount(responses=["Summary"])
    result = compress_pipeline_chat_history(
        pipeline_chat_history,
        llm,
        max_token_limit=20,
        input_messages=[],
        keep_history_len=5,
        history_mode=PipelineChatHistoryModes.MAX_HISTORY_LENGTH,
    )

    assert len(result) == 5
    assert [message.content for message in result] == [
        "Hello 1",
        "Hi 1",
        "Hello 2",
        "Hi 2",
        "Hello 3",
    ]
