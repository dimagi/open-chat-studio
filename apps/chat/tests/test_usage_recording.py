import pytest

from apps.accounting.models import UsageType
from apps.chat.bots import TopicBot
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.langchain import mock_experiment_llm


@pytest.mark.django_db()
def test_record_usage():
    session = ExperimentSessionFactory()
    with mock_experiment_llm(session.experiment, responses=["How can I help today?"], token_counts=[1]) as service:
        bot = TopicBot(session)
        response = bot.process_input("Hi")

    assert response == "How can I help today?"
    assert len(service.llm.get_calls()) == 1
    assert service.usage_recorder.totals == {
        UsageType.INPUT_TOKENS: 1,
        UsageType.OUTPUT_TOKENS: 1,
    }
