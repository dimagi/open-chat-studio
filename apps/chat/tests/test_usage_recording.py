import pytest

from apps.accounting.models import UsageType
from apps.accounting.tests.utils import assert_usage
from apps.accounting.usage import UsageRecorder
from apps.chat.bots import TopicBot
from apps.chat.tests.test_routing import _make_experiment_with_routing
from apps.experiments.models import SafetyLayer
from apps.service_providers.models import LlmProvider
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.langchain import mock_experiment_llm_service


@pytest.mark.django_db()
def test_record_usage():
    session = ExperimentSessionFactory()
    with mock_experiment_llm_service(["How can I help today?"], token_counts=[1]) as service:
        bot = TopicBot(session)
        response = bot.process_input("Hi")

    assert response == "How can I help today?"
    assert len(service.llm.get_calls()) == 1
    assert service.usage_recorder.totals == {
        UsageType.INPUT_TOKENS: 1,
        UsageType.OUTPUT_TOKENS: 1,
    }


@pytest.mark.django_db()
def test_record_usage_with_safety_layer():
    session = ExperimentSessionFactory()
    layer = SafetyLayer.objects.create(prompt_text="Is this message safe?", team=session.experiment.team)
    session.experiment.safety_layers.add(layer)

    with mock_experiment_llm_service(["safe", "How can I help today?"], token_counts=[1]) as service:
        bot = TopicBot(session)
        response = bot.process_input("Hi")

    assert response == "How can I help today?"
    assert len(service.llm.get_calls()) == 2
    assert service.usage_recorder.totals == {
        UsageType.INPUT_TOKENS: 2,
        UsageType.OUTPUT_TOKENS: 2,
    }


@pytest.mark.django_db()
def test_record_usage_with_routing():
    experiment = _make_experiment_with_routing()
    session = ExperimentSessionFactory(experiment=experiment)
    usage_recorder = UsageRecorder(LlmProvider(id=1, team_id=experiment.team_id))
    with mock_experiment_llm_service(
        ["keyword1", "How can I help today?"], token_counts=[1], usage_recorder=usage_recorder
    ) as service:
        bot = TopicBot(session)
        response = bot.process_input("Hi")
    assert response == "How can I help today?"
    assert len(service.llm.get_calls()) == 2
    assert service.usage_recorder.totals == {
        UsageType.INPUT_TOKENS: 2,
        UsageType.OUTPUT_TOKENS: 2,
    }
    assert_usage(
        session,
        [
            (UsageType.INPUT_TOKENS, 2),
            (UsageType.OUTPUT_TOKENS, 2),
        ],
    )
