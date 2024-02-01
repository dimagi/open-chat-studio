from typing import Any, Sequence

import pytest
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.service_providers.llm_service import LlmService
from apps.service_providers.llm_service.runnables import ChainOutput, SimpleExperimentRunnable
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.langchain import FakeLlm


class FakeLlmService(LlmService):
    llm: Any

    def get_chat_model(self, llm_model: str, temperature: float):
        return self.llm


@pytest.fixture()
def fake_llm():
    return FakeLlm(responses=["this is a test message"], token_counts=[30, 20, 10])


@pytest.fixture()
def experiment(fake_llm):
    experiment = ExperimentFactory()
    experiment.llm_provider.get_llm_service = lambda: FakeLlmService(llm=fake_llm)
    return experiment


@pytest.fixture()
def session(fake_llm):
    session = ExperimentSessionFactory()
    session.experiment.llm_provider.get_llm_service = lambda: FakeLlmService(llm=fake_llm)
    return session


@pytest.fixture()
def chat(team_with_users):
    chat = Chat.objects.create(team=team_with_users)
    ChatMessage.objects.create(chat=chat, content="Hello", message_type=ChatMessageType.HUMAN)
    return chat


@pytest.mark.django_db
def test_simple_experiment_runnable(experiment, fake_llm):
    runnable = SimpleExperimentRunnable(experiment=experiment)
    result = runnable.invoke({"input": "hi"})
    assert result == ChainOutput(output="this is a test message", prompt_tokens=30, completion_tokens=20)
    assert fake_llm.calls == [[SystemMessage(content=experiment.chatbot_prompt.prompt), HumanMessage(content="hi")]]


@pytest.mark.django_db
def test_simple_experiment_runnable_with_history(session, chat, fake_llm):
    experiment = session.experiment
    experiment.max_token_limit = 0  # disable compression
    session.chat = chat
    runnable = SimpleExperimentRunnable(experiment=experiment, session=session)
    result = runnable.invoke({"input": "hi"})
    assert result == ChainOutput(output="this is a test message", prompt_tokens=30, completion_tokens=20)
    assert len(fake_llm.calls) == 1
    assert _messages_to_dict(fake_llm.calls[0]) == [
        {"system": experiment.chatbot_prompt.prompt},
        {"human": "Hello"},
        {"human": "hi"},
    ]


def _messages_to_dict(messages: Sequence[BaseMessage]) -> list[dict]:
    return [{message.type: message.content} for message in messages]
