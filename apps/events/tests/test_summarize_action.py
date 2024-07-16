from unittest.mock import Mock

import pytest
from langchain_core.messages import messages_from_dict

from apps.accounting.models import UsageType
from apps.events.actions import SummarizeConversationAction
from apps.events.models import EventAction
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.langchain import mock_experiment_llm_service


@pytest.mark.django_db()
def test_summarize_action():
    session = ExperimentSessionFactory()
    session.chat.get_langchain_messages_until_summary = Mock(
        return_value=messages_from_dict(
            [
                {"type": "ai", "data": {"content": "How can I help today?"}},
                {"type": "human", "data": {"content": "I need help with something"}},
            ]
        )
    )
    action = EventAction(id=1)
    with mock_experiment_llm_service(["summary"]) as service:
        result = SummarizeConversationAction().invoke(session, action)
    assert result == "summary"
    assert service.usage_recorder.totals == {UsageType.INPUT_TOKENS: 1, UsageType.OUTPUT_TOKENS: 1}
