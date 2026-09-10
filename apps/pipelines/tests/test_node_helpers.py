from unittest.mock import Mock

import pytest
from langchain.agents.middleware import ToolErrorMiddleware
from langchain_core.messages import SystemMessage

from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.models import ExperimentSession
from apps.pipelines.nodes.helpers import get_agent_middleware, temporary_session
from apps.utils.factories.team import TeamFactory, UserFactory


@pytest.mark.django_db()
def test_temporary_session_is_temporary():
    session_id = None
    user = UserFactory.create()
    with temporary_session(TeamFactory.create(), user.id) as session:
        session_id = session.id
        message = session.chat.messages.create(message_type=ChatMessageType.HUMAN, content="Hello, world!")

    assert not ExperimentSession.objects.filter(id=session_id).exists()
    assert not ChatMessage.objects.filter(id=message.id).exists()


@pytest.mark.django_db()
def test_temporary_session_rolls_back_on_error():
    user = UserFactory.create()

    def _run_with_temp_session():
        with temporary_session(TeamFactory.create(), user.id):
            raise Exception("error")

    with pytest.raises(Exception, match="error"):
        _run_with_temp_session()

    assert ExperimentSession.objects.count() == 0


class TestGetAgentMiddlewareToolErrorHandling:
    """A tool exception should degrade to a soft-fail ToolMessage, not abort the turn."""

    def _build_middleware(self):
        node = Mock()
        node.build_history_middleware.return_value = None
        node.get_llm_service.return_value.get_prompt_caching_middleware.return_value = None
        return get_agent_middleware(node, SystemMessage(content="prompt"))

    def test_includes_tool_error_middleware(self):
        middleware = self._build_middleware()
        assert any(isinstance(m, ToolErrorMiddleware) for m in middleware)

    def test_on_error_returns_content_instead_of_propagating(self):
        (tool_error_middleware,) = [m for m in self._build_middleware() if isinstance(m, ToolErrorMiddleware)]
        request = Mock(tool_call={"name": "test_tool"})

        content = tool_error_middleware.on_error(ValueError("boom"), request)

        assert content is not None
        assert "boom" not in content
        assert "ValueError" in content
