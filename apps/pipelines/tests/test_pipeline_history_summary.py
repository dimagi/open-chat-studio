import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from apps.pipelines.models import PipelineChatHistoryModes, PipelineChatHistoryTypes
from apps.utils.factories.experiment import (
    ExperimentSessionFactory,
)
from apps.utils.factories.pipelines import PipelineChatHistoryFactory
from apps.utils.factories.service_provider_factories import LlmProviderFactory
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
