import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from apps.chat.bots import TopicBot
from apps.chat.models import ChatMessageType
from apps.experiments.models import ExperimentRoute
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.langchain import mock_experiment_llm_service


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("with_default", "routing_response", "expected_tag"),
    [
        (True, "keyword1", "keyword1"),
        (True, "keyword3", "keyword3"),
        (True, "not a valid keyword", "keyword2"),
        (False, "keyword2", "keyword2"),
        (False, "not a valid keyword", "keyword1"),
    ],
)
def test_experiment_routing(with_default, routing_response, expected_tag):
    experiment = _make_experiment_with_routing(with_default)
    session = ExperimentSessionFactory(experiment=experiment)
    with mock_experiment_llm_service([routing_response, "How can I help today?"], token_counts=[1]) as service:
        bot = TopicBot(session)
        response = bot.process_input("Hi")
    assert response == "How can I help today?"
    assert service.llm.get_call_messages() == [
        [SystemMessage(content="You are a helpful assistant"), HumanMessage(content="Hi")],
        [SystemMessage(content="You are a helpful assistant"), HumanMessage(content="Hi")],
    ]
    message = session.chat.messages.filter(message_type=ChatMessageType.AI).first()
    assert list(message.tags.values_list("name", flat=True)) == [expected_tag]


def _make_experiment_with_routing(with_default=True):
    team = TeamFactory()
    experiments = ExperimentFactory.create_batch(4, team=team)
    ExperimentRoute.objects.bulk_create(
        [
            ExperimentRoute(
                team=team, parent=experiments[0], child=experiments[1], keyword="keyword1", is_default=False
            ),
            ExperimentRoute(
                # make the middle one the default to avoid first / last false positives
                team=team,
                parent=experiments[0],
                child=experiments[2],
                keyword="keyword2",
                is_default=with_default,
            ),
            ExperimentRoute(
                team=team, parent=experiments[0], child=experiments[3], keyword="keyword3", is_default=False
            ),
        ]
    )
    return experiments[0]
