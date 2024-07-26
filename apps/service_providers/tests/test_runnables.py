import dataclasses
from collections.abc import Sequence
from unittest.mock import patch

import freezegun
import pytest
from langchain_core.messages import BaseMessage, SystemMessage

from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.experiments.models import AgentTools, SourceMaterial
from apps.service_providers.llm_service.runnables import (
    AgentExperimentRunnable,
    ChainOutput,
    ExperimentRunnable,
    SimpleExperimentRunnable,
)
from apps.service_providers.llm_service.state import ChatExperimentState
from apps.utils.factories.channels import ChannelPlatform, ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.langchain import build_fake_llm_service


@pytest.fixture()
def fake_llm_service():
    return build_fake_llm_service(responses=["this is a test message"], token_counts=[30, 20, 10])


@pytest.fixture()
def session(fake_llm_service):
    session = ExperimentSessionFactory()
    session.experiment.get_llm_service = lambda: fake_llm_service
    session.experiment.tools = [AgentTools.SCHEDULE_UPDATE]
    session.get_participant_data = lambda *args, **kwargs: {"name": "Tester"}
    return session


@pytest.fixture()
def chat(team_with_users):
    chat = Chat.objects.create(team=team_with_users)
    ChatMessage.objects.create(chat=chat, content="Hello", message_type=ChatMessageType.HUMAN)
    return chat


@dataclasses.dataclass
class RunnableFixture:
    runnable: type[ExperimentRunnable]
    expect_tools: bool = False

    def build(self, *args, **kwargs):
        return self.runnable(*args, **kwargs)


runnables = {
    "simple": RunnableFixture(SimpleExperimentRunnable),
    "agent": RunnableFixture(AgentExperimentRunnable, expect_tools=True),
}


@pytest.fixture(params=list(runnables))
def runnable(request, session):
    return runnables[request.param]


@pytest.mark.django_db()
@freezegun.freeze_time("2024-02-08 13:00:08.877096+00:00")
def test_runnable(runnable, session, fake_llm_service):
    chain = runnable.build(state=ChatExperimentState(session.experiment, session))
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
@freezegun.freeze_time("2024-02-08 13:00:08.877096+00:00")
def test_runnable_with_source_material(runnable, session, fake_llm_service):
    session.experiment.source_material = SourceMaterial(material="this is the source material")
    session.experiment.prompt_text = "System prompt with {source_material}"
    chain = runnable.build(state=ChatExperimentState(session.experiment, session))
    result = chain.invoke("hi")
    assert result == ChainOutput(output="this is a test message", prompt_tokens=30, completion_tokens=20)
    expected_system__prompt = "System prompt with this is the source material"
    assert fake_llm_service.llm.get_call_messages()[0][0] == SystemMessage(content=expected_system__prompt)


@pytest.mark.django_db()
@freezegun.freeze_time("2024-02-08 13:00:08.877096+00:00")
def test_runnable_with_source_material_missing(runnable, session, fake_llm_service):
    session.experiment.prompt_text = "System prompt with {source_material}"
    chain = runnable.build(state=ChatExperimentState(session.experiment, session))
    result = chain.invoke("hi")
    assert result == ChainOutput(output="this is a test message", prompt_tokens=30, completion_tokens=20)
    expected_system__prompt = "System prompt with "
    assert fake_llm_service.llm.get_call_messages()[0][0] == SystemMessage(content=expected_system__prompt)


@pytest.mark.django_db()
def test_runnable_runnable_format_input(runnable, session, fake_llm_service):
    chain = runnable.build(state=ChatExperimentState(session.experiment, session))
    session.experiment.input_formatter = "foo {input} bar"
    result = chain.invoke("hi")
    assert result == ChainOutput(output="this is a test message", prompt_tokens=30, completion_tokens=20)
    assert len(fake_llm_service.llm.get_calls()) == 1
    assert _messages_to_dict(fake_llm_service.llm.get_call_messages()[0])[1] == {"human": "foo hi bar"}


@pytest.mark.django_db()
def test_runnable_save_input_to_history(runnable, session, chat, fake_llm_service):
    chain = runnable.build(state=ChatExperimentState(session.experiment, session))
    session.chat = chat
    assert chat.messages.count() == 1

    result = chain.invoke("hi", config={"configurable": {"save_input_to_history": False}})

    assert result.output == "this is a test message"
    assert len(fake_llm_service.llm.get_calls()) == 1
    assert chat.messages.count() == 2


@pytest.mark.django_db()
@freezegun.freeze_time("2024-02-08 13:00:08.877096+00:00")
def test_runnable_with_history(runnable, session, chat, fake_llm_service):
    experiment = session.experiment
    experiment.max_token_limit = 0  # disable compression
    session.chat = chat
    assert chat.messages.count() == 1
    chain = runnable.build(state=ChatExperimentState(session.experiment, session))
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
@freezegun.freeze_time("2024-02-08 13:00:08.877096+00:00")
@pytest.mark.parametrize(
    ("participant_with_user", "is_web_session", "considered_authorized"),
    [(True, True, True), (False, True, False), (True, False, True), (False, False, True)],
)
@patch("apps.channels.forms.TelegramChannelForm._set_telegram_webhook")
def test_runnable_with_participant_data(
    _set_telegram_webhook,
    participant_with_user,
    is_web_session,
    considered_authorized,
    runnable,
    session,
    fake_llm_service,
):
    """Participant data should be included in the prompt only for authorized users"""
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
    chain = runnable.build(state=ChatExperimentState(session.experiment, session))
    chain.invoke("hi")

    if considered_authorized:
        expected_prompt = "System prompt. Participant data: {'name': 'Tester'}"
    else:
        expected_prompt = "System prompt. Participant data: "
    assert fake_llm_service.llm.get_call_messages()[0][0] == SystemMessage(content=expected_prompt)


@pytest.mark.django_db()
@freezegun.freeze_time("2024-02-08 13:00:08.877096+00:00")
def test_runnable_with_current_datetime(runnable, session, fake_llm_service):
    session.experiment.source_material = SourceMaterial(material="this is the source material")
    session.experiment.prompt_text = "System prompt with current datetime: {current_datetime}"
    chain = runnable.build(state=ChatExperimentState(session.experiment, session))
    result = chain.invoke("hi")
    assert result == ChainOutput(output="this is a test message", prompt_tokens=30, completion_tokens=20)
    expected_system__prompt = "System prompt with current datetime: Thursday, 08 February 2024 13:00:08 UTC"
    assert fake_llm_service.llm.get_call_messages()[0][0] == SystemMessage(content=expected_system__prompt)


def _messages_to_dict(messages: Sequence[BaseMessage]) -> list[dict]:
    return [{message.type: message.content} for message in messages]
