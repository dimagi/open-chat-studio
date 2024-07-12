from unittest.mock import patch

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from apps.accounting.models import UsageType
from apps.chat.bots import TopicBot
from apps.service_providers.llm_service.callbacks import TokenCountingCallbackHandler, UsageCallbackHandler
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.langchain import FakeLlm, FakeLlmService, FakeUsageRecorder


@pytest.mark.django_db()
def test_record_usage():
    session = ExperimentSessionFactory()
    usage_recorder = FakeUsageRecorder()
    fake_llm = FakeLlm(responses=["How can I help today?"], token_counts=[1])
    fake_llm.callbacks = [
        UsageCallbackHandler(usage_recorder, TokenCountingCallbackHandler(fake_llm)),
    ]

    service = FakeLlmService(llm=fake_llm, usage_recorder=usage_recorder)
    with patch("apps.experiments.models.Experiment.get_llm_service", new=lambda x: service):
        bot = TopicBot(session)
        response = bot.process_input("Hi")

    assert response == "How can I help today?"
    assert fake_llm.get_call_messages() == [
        [SystemMessage(content="You are a helpful assistant"), HumanMessage(content="Hi")],
    ]
    assert usage_recorder.totals == {
        UsageType.INPUT_TOKENS: 1,
        UsageType.OUTPUT_TOKENS: 1,
    }
