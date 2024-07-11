from unittest.mock import patch

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from apps.chat.bots import TopicBot
from apps.chat.models import ChatMessageType
from apps.experiments.models import ExperimentRoute
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.langchain import FakeLlm, FakeLlmService


@pytest.fixture()
def experiment():
    team = TeamFactory()
    experiments = ExperimentFactory.create_batch(3, team=team)
    ExperimentRoute.objects.bulk_create(
        [
            ExperimentRoute(
                team=team, parent=experiments[0], child=experiments[1], keyword="keyword1", is_default=False
            ),
            ExperimentRoute(
                team=team, parent=experiments[0], child=experiments[2], keyword="keyword2", is_default=True
            ),
        ]
    )
    return experiments[0]


@pytest.mark.django_db()
def test_routing(experiment):
    session = ExperimentSessionFactory(experiment=experiment)
    fake_llm = FakeLlm(responses=["keyword2", "How can I help today?"], token_counts=[0])
    fake_service = FakeLlmService(llm=fake_llm)
    with patch("apps.experiments.models.Experiment.get_llm_service", new=lambda x: fake_service):
        bot = TopicBot(session)
        response = bot.process_input("Hi")
    assert response == "How can I help today?"
    assert fake_llm.get_call_messages() == [
        [SystemMessage(content="You are a helpful assistant"), HumanMessage(content="Hi")],
        [SystemMessage(content="You are a helpful assistant"), HumanMessage(content="Hi")],
    ]
    message = session.chat.messages.filter(message_type=ChatMessageType.AI).first()
    assert list(message.tags.values_list("name", flat=True)) == ["keyword2"]
