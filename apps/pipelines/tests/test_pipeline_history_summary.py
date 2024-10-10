from unittest import mock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from apps.chat.conversation import compress_pipeline_chat_history
from apps.pipelines.models import PipelineChatHistoryTypes, PipelineChatMessages
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


@django_db_with_data(available_apps=("apps.service_providers",))
def test_no_summary_returns_all_messages(experiment_session):
    history = experiment_session.pipeline_chat_history.create(type=PipelineChatHistoryTypes.NAMED, name="name")
    message1 = history.messages.create(ai_message="I am a robot", human_message="hi, please fetch me a coffee")
    message2 = history.messages.create(ai_message="I can't do that", human_message="sudo, please fetch me a coffee")
    expected_messages = [
        AIMessage(content="I can't do that", additional_kwargs={"id": message2.id}),
        HumanMessage(content="sudo, please fetch me a coffee", additional_kwargs={"id": message2.id}),
        AIMessage(content="I am a robot", additional_kwargs={"id": message1.id}),
        HumanMessage(content="hi, please fetch me a coffee", additional_kwargs={"id": message1.id}),
    ]
    summary_messages = history.get_langchain_messages_until_summary()
    assert expected_messages == summary_messages


@django_db_with_data(available_apps=("apps.service_providers",))
def test_compress_history_no_need_for_compression(pipeline_chat_history):
    for i in range(15):
        # (3 tokens for human messages + 3 tokens for ai messages) * 15 messages)
        # = 90 tokens in total
        pipeline_chat_history.messages.create(human_message=f"Hello {i}", ai_message=f"Hello {i}")

    pipeline_chat_history.token_limit = 90
    compress_pipeline_chat_history(
        pipeline_chat_history, FakeLlmSimpleTokenCount(responses=["Summary"]), input_messages=[]
    )
    messages = pipeline_chat_history.get_langchain_messages_until_summary()
    # No summary messages
    assert not any(isinstance(message, SystemMessage) for message in messages)


@django_db_with_data(available_apps=("apps.service_providers",))
@mock.patch("apps.chat.conversation._get_new_summary")
def test_create_summary_token_limit_reached(mock_get_new_summary, pipeline_chat_history):
    summary_message = "Summary"  # 2 tokens
    mock_get_new_summary.return_value = summary_message

    for i in range(15):
        # (3 tokens for human messages + 3 tokens for ai messages) * 15 messages)
        # = 90 tokens in total
        pipeline_chat_history.messages.create(human_message=f"Hello {i}", ai_message=f"Hello {i}")

    pipeline_chat_history.token_limit = 80
    compressed_history = compress_pipeline_chat_history(
        pipeline_chat_history, FakeLlmSimpleTokenCount(responses=["Summary"]), input_messages=[]
    )
    assert isinstance(compressed_history[0], SystemMessage)
    assert compressed_history[0].content == summary_message
    assert PipelineChatMessages.objects.get(id=compressed_history[1].additional_kwargs["id"]).summary == "Summary"

    summary_messages = pipeline_chat_history.get_langchain_messages_until_summary()
    assert isinstance(summary_messages[-1], HumanMessage)
    assert isinstance(summary_messages[-2], AIMessage)
    assert isinstance(summary_messages[-3], SystemMessage)
