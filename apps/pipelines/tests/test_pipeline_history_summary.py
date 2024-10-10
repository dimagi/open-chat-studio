from langchain_core.messages import HumanMessage, AIMessage
import pytest

from apps.chat.conversation import compress_pipeline_chat_history
from apps.pipelines.models import PipelineChatHistoryTypes
from apps.utils.factories.experiment import (
    ExperimentSessionFactory,
)
from apps.utils.factories.service_provider_factories import LlmProviderFactory
from apps.utils.langchain import FakeLlmEcho
from apps.utils.pytest import django_db_with_data


@pytest.fixture()
def provider():
    return LlmProviderFactory()


@pytest.fixture()
def experiment_session():
    return ExperimentSessionFactory()


@django_db_with_data(available_apps=("apps.service_providers",))
def test_empty_summaries_returns_all_messages(experiment_session):
    history = experiment_session.pipeline_chat_history.create(type=PipelineChatHistoryTypes.NAMED, name="name")
    message1 = history.messages.create(ai_message="I am a robot", human_message="hi, please fetch me a coffee")
    message2 = history.messages.create(ai_message="I can't do that", human_message="sudo, please fetch me a coffee")

    expected_messages = [
        HumanMessage(content="hi, please fetch me a coffee", additional_kwargs={"id": message1.id}),
        AIMessage(content="I am a robot", additional_kwargs={"id": message1.id}),
        HumanMessage(content="sudo, please fetch me a coffee", additional_kwargs={"id": message2.id}),
        AIMessage(content="I can't do that", additional_kwargs={"id": message2.id}),
    ]
    summary_messages = history.get_messages_until_summary()
    assert expected_messages == summary_messages

@django_db_with_data(available_apps=("apps.service_providers",))
def test_create_summary_token_limit_reached(experiment_session):
    history = experiment_session.pipeline_chat_history.create(type=PipelineChatHistoryTypes.NAMED, name="name")
    history.token_limit = 35
    history.messages.create(ai_message="I am a robot", human_message="hi, please fetch me a coffee")
    history.messages.create(ai_message="I can't do that", human_message="sudo, please fetch me a coffee")

    expected_messages = [
        HumanMessage(content="hi, please fetch me a coffee"),
        AIMessage(content="I am a robot"),
        HumanMessage(content="sudo, please fetch me a coffee"),
        AIMessage(content="I can't do that"),
    ]
    compress_pipeline_chat_history(history, FakeLlmEcho(), expected_messages)
    summary_messages = history.get_messages_until_summary()
    assert expected_messages == summary_messages


def test_get_latest_summary_new_messages():
    pass
