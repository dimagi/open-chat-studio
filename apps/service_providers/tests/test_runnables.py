import dataclasses
from collections.abc import Sequence
from datetime import datetime
from unittest import mock
from unittest.mock import patch

import pytest
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from apps.annotations.models import TagCategories
from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.custom_actions.models import CustomAction, CustomActionOperation
from apps.experiments.models import AgentTools
from apps.service_providers.llm_service.history_managers import ExperimentHistoryManager
from apps.service_providers.llm_service.runnables import (
    AgentLLMChat,
    ChainOutput,
    ChatAdapter,
    LLMChat,
    SimpleLLMChat,
)
from apps.service_providers.tracing import TracingService
from apps.utils.factories.channels import ChannelPlatform, ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.langchain import build_fake_llm_service
from apps.utils.time import pretty_date


@pytest.fixture()
def fake_llm_service():
    return build_fake_llm_service(responses=["this is a test message"], token_counts=[30, 20, 10])


@pytest.fixture()
def session(fake_llm_service):
    session = ExperimentSessionFactory()
    session.experiment.get_llm_service = lambda: fake_llm_service

    session.experiment.tools = [AgentTools.MOVE_SCHEDULED_MESSAGE_DATE]
    proxy_mock = mock.Mock()
    proxy_mock.get.return_value = {"name": "Tester"}
    proxy_mock.get_schedules.return_value = []
    with patch("apps.service_providers.llm_service.prompt_context.ParticipantDataProxy", return_value=proxy_mock):
        yield session


@pytest.fixture()
def chat(team_with_users):
    chat = Chat.objects.create(team=team_with_users)
    ChatMessage.objects.create(chat=chat, content="Hello", message_type=ChatMessageType.HUMAN)
    return chat


@dataclasses.dataclass
class RunnableFixture:
    runnable: type[LLMChat]
    expect_tools: bool = False

    def build(self, *args, **kwargs):
        return self.runnable(*args, **kwargs)


runnables = {
    "simple": RunnableFixture(SimpleLLMChat),
    "agent": RunnableFixture(AgentLLMChat, expect_tools=True),
}


@pytest.fixture(params=list(runnables))
def runnable(request, session):
    return runnables[request.param]


def _get_history_manager(session, experiment=None):
    return ExperimentHistoryManager.for_llm_chat(
        session=session,
        experiment=experiment if experiment else session.experiment,
        trace_service=TracingService.empty(),
    )


@pytest.mark.django_db()
def test_runnable(runnable, session, fake_llm_service):
    history_manager = _get_history_manager(session)
    chain = runnable.build(
        adapter=ChatAdapter.for_experiment(session.experiment, session), history_manager=history_manager
    )
    result = chain.invoke("hi")
    assert result == ChainOutput(output="this is a test message", prompt_tokens=30, completion_tokens=20)
    assert len(fake_llm_service.llm.get_calls()) == 1
    assert _messages_to_dict(fake_llm_service.llm.get_call_messages()[0]) == [
        {"system": "You are a helpful assistant"},
        {"human": "hi"},
    ]
    if runnable.expect_tools:
        assert "tools" in fake_llm_service.llm.get_calls()[0].kwargs
    else:
        assert "tools" not in fake_llm_service.llm.get_calls()[0].kwargs


@pytest.mark.django_db()
def test_bot_message_is_tagged_with_experiment_version(runnable, session, fake_llm_service):
    experiment_version = session.experiment.create_new_version()
    experiment_version.get_llm_service = lambda: fake_llm_service
    history_manager = _get_history_manager(session, experiment_version)
    chain = runnable.build(
        adapter=ChatAdapter.for_experiment(experiment_version, session), history_manager=history_manager
    )
    chain.invoke("hi")
    ai_message = session.chat.messages.get(message_type=ChatMessageType.AI)
    tag = ai_message.tags.first()
    assert tag.name == "v1"
    assert tag.category == TagCategories.EXPERIMENT_VERSION


@pytest.mark.django_db()
def test_runnable_with_source_material(runnable, session, fake_llm_service):
    session.experiment.prompt_text = "System prompt with {source_material}"
    adapter = ChatAdapter.for_experiment(session.experiment, session)
    adapter.template_context.get_source_material = mock.Mock(return_value="this is the source material")
    history_manager = _get_history_manager(session)
    chain = runnable.build(adapter=adapter, history_manager=history_manager)
    result = chain.invoke("hi")
    assert result == ChainOutput(output="this is a test message", prompt_tokens=30, completion_tokens=20)
    expected_system__prompt = "System prompt with this is the source material"
    assert fake_llm_service.llm.get_call_messages()[0][0] == SystemMessage(content=expected_system__prompt)


@pytest.mark.django_db()
def test_runnable_with_source_material_missing(runnable, session, fake_llm_service):
    session.experiment.prompt_text = "System prompt with {source_material}"
    history_manager = _get_history_manager(session)
    chain = runnable.build(
        adapter=ChatAdapter.for_experiment(session.experiment, session), history_manager=history_manager
    )
    result = chain.invoke("hi")
    assert result == ChainOutput(output="this is a test message", prompt_tokens=30, completion_tokens=20)
    expected_system__prompt = "System prompt with "
    assert fake_llm_service.llm.get_call_messages()[0][0] == SystemMessage(content=expected_system__prompt)


@pytest.mark.django_db()
def test_runnable_with_custom_actions(session, fake_llm_service):
    action = CustomAction.objects.create(
        team=session.team,
        name="Custom Action",
        description="Custom action description",
        prompt="Custom action prompt",
        api_schema={
            "openapi": "3.0.0",
            "info": {"title": "Weather API", "version": "1.0.0"},
            "servers": [{"url": "https://api.weather.com"}],
            "paths": {
                "/weather": {
                    "get": {
                        "summary": "Get weather",
                    },
                    "post": {
                        "summary": "Update weather",
                    },
                },
                "/pollen": {
                    "get": {
                        "summary": "Get pollen count",
                    }
                },
            },
        },
        allowed_operations=["weather_get"],
    )
    CustomActionOperation.objects.create(
        custom_action=action, experiment=session.experiment, operation_id="weather_get"
    )
    CustomActionOperation.objects.create(custom_action=action, experiment=session.experiment, operation_id="pollen_get")
    session.experiment.tools = []
    adapter = ChatAdapter.for_experiment(session.experiment, session)
    history_manager = _get_history_manager(session)
    chain = AgentLLMChat(adapter=adapter, history_manager=history_manager)
    result = chain.invoke("hi")
    assert result == ChainOutput(output="this is a test message", prompt_tokens=30, completion_tokens=20)
    messages = fake_llm_service.llm.get_calls()[0].args[0]
    assert len(messages) == 2
    assert messages == [
        SystemMessage(content="You are a helpful assistant"),
        HumanMessage(content="hi"),
    ]

    tools_ = fake_llm_service.llm.get_calls()[0].kwargs["tools"]
    # we only expect one because the other one is not present in the action's allowed_operations
    assert len(tools_) == 1, tools_
    assert sorted([tool["function"]["name"] for tool in tools_]) == ["weather_get"]


@pytest.mark.parametrize(
    ("extra_var", "extra_output"),
    [
        ("", ""),
        (" {participant_data}", " {'name': 'Tester'}"),
        (" {current_datetime}", " the current date and time"),
        (" {participant_data[name]}", " Tester"),
    ],
)
@pytest.mark.django_db()
def test_runnable_runnable_format_input(runnable, session, fake_llm_service, extra_var, extra_output):
    session.experiment.input_formatter = "foo {input} bar" + extra_var
    adapter = ChatAdapter.for_experiment(session.experiment, session)
    adapter.template_context.get_current_datetime = mock.Mock(return_value="the current date and time")
    history_manager = _get_history_manager(session)
    chain = runnable.build(adapter=adapter, history_manager=history_manager)
    result = chain.invoke("hi")
    assert result == ChainOutput(output="this is a test message", prompt_tokens=30, completion_tokens=20)
    assert len(fake_llm_service.llm.get_calls()) == 1
    assert _messages_to_dict(fake_llm_service.llm.get_call_messages()[0])[1] == {"human": "foo hi bar" + extra_output}


@pytest.mark.django_db()
def test_runnable_save_input_to_history(runnable, session, chat, fake_llm_service):
    history_manager = _get_history_manager(session)
    chain = runnable.build(
        adapter=ChatAdapter.for_experiment(session.experiment, session), history_manager=history_manager
    )
    session.chat = chat
    assert chat.messages.count() == 1

    result = chain.invoke("hi", config={"configurable": {"save_input_to_history": False}})

    assert result.output == "this is a test message"
    assert len(fake_llm_service.llm.get_calls()) == 1
    assert chat.messages.count() == 2


@pytest.mark.django_db()
def test_runnable_exclude_conversation_history(runnable, session, chat, fake_llm_service):
    history_manager = _get_history_manager(session)
    chain = runnable.build(
        adapter=ChatAdapter.for_experiment(session.experiment, session), history_manager=history_manager
    )
    session.chat = chat
    assert chat.messages.count() == 1
    # The existing message should not be included in the LLM call, only the system message an human message
    result = chain.invoke("hi", config={"configurable": {"include_conversation_history": False}})

    assert result.output == "this is a test message"
    assert fake_llm_service.llm.get_calls()[0].args[0] == [
        SystemMessage(content="You are a helpful assistant"),
        HumanMessage(content="hi"),
    ]
    assert len(fake_llm_service.llm.get_calls()) == 1
    assert chat.messages.count() == 3


@pytest.mark.django_db()
def test_runnable_with_history(runnable, session, chat, fake_llm_service):
    experiment = session.experiment
    experiment.llm_provider_model.max_token_limit = 0  # disable compression
    session.chat = chat
    assert chat.messages.count() == 1
    history_manager = _get_history_manager(session)
    chain = runnable.build(
        adapter=ChatAdapter.for_experiment(session.experiment, session), history_manager=history_manager
    )
    result = chain.invoke("hi")
    assert result == ChainOutput(output="this is a test message", prompt_tokens=30, completion_tokens=20)
    assert len(fake_llm_service.llm.get_calls()) == 1
    assert _messages_to_dict(fake_llm_service.llm.get_call_messages()[0]) == [
        {"system": experiment.prompt_text},
        {"human": "Hello"},
        {"human": "hi"},
    ]
    assert chat.messages.count() == 3


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("participant_with_user", "is_web_session"),
    [(True, True), (False, True), (True, False), (False, False)],
)
@patch("apps.channels.forms.TelegramChannelForm._set_telegram_webhook")
def test_runnable_with_participant_data(
    _set_telegram_webhook,
    participant_with_user,
    is_web_session,
    runnable,
    session,
    fake_llm_service,
):
    """Participant data should be included in the prompt"""
    session.experiment_channel = ExperimentChannelFactory(
        experiment=session.experiment, platform=ChannelPlatform.WEB if is_web_session else ChannelPlatform.TELEGRAM
    )
    session.save()
    session.experiment.save()

    participant = session.participant
    if participant_with_user:
        participant.user = session.experiment.owner
    else:
        participant.user = None
    participant.save()

    session.experiment.prompt_text = "System prompt. Participant data: {participant_data}"
    history_manager = _get_history_manager(session)
    chain = runnable.build(
        adapter=ChatAdapter.for_experiment(session.experiment, session), history_manager=history_manager
    )
    chain.invoke("hi")

    expected_prompt = "System prompt. Participant data: {'name': 'Tester'}"
    assert fake_llm_service.llm.get_call_messages()[0][0] == SystemMessage(content=expected_prompt)


@pytest.mark.django_db()
def test_runnable_with_current_datetime(runnable, session, fake_llm_service):
    session.experiment.prompt_text = "System prompt with current datetime: {current_datetime}"
    adapter = ChatAdapter.for_experiment(session.experiment, session)
    adapter.template_context.get_current_datetime = mock.Mock(
        return_value=pretty_date(datetime.fromisoformat("2024-02-08 13:00:08.877096+00:00"))
    )
    history_manager = _get_history_manager(session)
    chain = runnable.build(adapter=adapter, history_manager=history_manager)
    result = chain.invoke("hi")
    assert result == ChainOutput(output="this is a test message", prompt_tokens=30, completion_tokens=20)
    expected_system__prompt = "System prompt with current datetime: Thursday, 08 February 2024 13:00:08 UTC"
    assert fake_llm_service.llm.get_call_messages()[0][0] == SystemMessage(content=expected_system__prompt)


def _messages_to_dict(messages: Sequence[BaseMessage]) -> list[dict]:
    return [{message.type: message.content} for message in messages]


@pytest.mark.django_db()
@patch("apps.service_providers.llm_service.runnables.LLMChat._populate_memory")
def test_input_message_is_saved_on_chain_error(populate_memory, runnable, session):
    populate_memory.side_effect = Exception("Error")
    history_manager = _get_history_manager(session)
    chain = runnable.build(
        adapter=ChatAdapter.for_experiment(session.experiment, session), history_manager=history_manager
    )
    with pytest.raises(Exception, match="Error"):
        chain.invoke("hi")
    assert ChatMessage.objects.filter(chat__experiment_session=session).count() == 1
    assert ChatMessage.objects.filter(chat__experiment_session=session, message_type=ChatMessageType.HUMAN).count() == 1
