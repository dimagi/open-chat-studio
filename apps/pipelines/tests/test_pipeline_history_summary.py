import pytest

from apps.pipelines.models import PipelineChatHistoryTypes
from apps.utils.factories.experiment import (
    ExperimentSessionFactory,
)
from apps.utils.factories.service_provider_factories import LlmProviderFactory
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
    messages = [
        history.messages.create(ai_message="I am a robot", human_message="hi, please fetch me a coffee"),
        history.messages.create(ai_message="I can't do that", human_message="sudo, please fetch me a coffee"),
    ]

    summary_messages = history.get_messages_until_summary()
    assert sorted(summary_messages, key=lambda m: m.id) == sorted(messages, key=lambda m: m.id)


def test_create_summary_token_limit_reached():
    pass


def test_get_latest_summary_new_messages():
    pass
