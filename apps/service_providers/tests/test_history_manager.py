import pytest

from apps.pipelines.models import PipelineChatHistoryTypes
from apps.service_providers.llm_service.history_managers import PipelineHistoryManager
from apps.utils.factories.experiment import ExperimentSessionFactory


@pytest.fixture()
def mock_session():
    return ExperimentSessionFactory()


@pytest.mark.django_db()
def test_returns_empty_list_when_history_type_is_none(mock_session):
    manager = PipelineHistoryManager(session=mock_session, history_type=PipelineChatHistoryTypes.NONE)
    assert manager.get_chat_history([]) == []


@pytest.mark.django_db()
@pytest.mark.parametrize("history_type", [PipelineChatHistoryTypes.GLOBAL, PipelineChatHistoryTypes.NAMED])
def test_returns_empty_list_when_session_is_none(mock_session, history_type):
    manager = PipelineHistoryManager(session=mock_session, history_type=history_type, max_token_limit=100)
    assert manager.get_chat_history([]) == []


@pytest.mark.django_db()
def test_adds_messages_to_history(mock_session):
    manager = PipelineHistoryManager(
        session=mock_session, history_type=PipelineChatHistoryTypes.NAMED, node_id="test_node", history_name="test_name"
    )
    manager.add_messages_to_history("input", {}, "output", {})
    history = mock_session.pipeline_chat_history.get(type=PipelineChatHistoryTypes.NAMED, name="test_name")
    assert history.messages.count() == 1


@pytest.mark.django_db()
def test_adds_messages_to_history_with_no_ai_message(mock_session):
    manager = PipelineHistoryManager(
        session=mock_session, history_type=PipelineChatHistoryTypes.NAMED, node_id="test_node", history_name="test_name"
    )
    manager.add_messages_to_history("input", {}, None, {})
    history = mock_session.pipeline_chat_history.get(type=PipelineChatHistoryTypes.NAMED, name="test_name")
    assert history.messages.count() == 1


@pytest.mark.django_db()
@pytest.mark.parametrize("history_type", [PipelineChatHistoryTypes.NONE, PipelineChatHistoryTypes.GLOBAL])
def test_does_not_add_messages(mock_session, history_type):
    manager = PipelineHistoryManager(session=mock_session, history_type=history_type)
    manager.add_messages_to_history("input", {}, "output", {})
    assert not mock_session.pipeline_chat_history.exists()
