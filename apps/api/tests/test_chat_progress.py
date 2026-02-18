from unittest import mock

import pytest
from django.core.cache import cache

from apps.api.views.chat import get_progress_message, get_progress_messages


@pytest.fixture(autouse=True)
def _clear_cache():
    yield
    cache.clear()


class TestGetProgressMessages:
    @mock.patch("apps.help.agents.progress_messages.build_system_agent")
    def test_returns_messages_on_success(self, mock_build_agent):
        mock_agent = mock.Mock()
        mock_agent.invoke.return_value = {"structured_response": mock.Mock(messages=["Thinking...", "Almost there..."])}
        mock_build_agent.return_value = mock_agent

        result = get_progress_messages("TestBot", "A test bot")

        assert result == ["Thinking...", "Almost there..."]

    @mock.patch("apps.help.agents.progress_messages.build_system_agent")
    def test_returns_empty_list_on_agent_build_failure(self, mock_build_agent):
        mock_build_agent.side_effect = Exception("no system agent models configured")

        result = get_progress_messages("TestBot", "A test bot")

        assert result == []

    @mock.patch("apps.help.agents.progress_messages.build_system_agent")
    def test_returns_empty_list_on_invoke_failure(self, mock_build_agent):
        mock_agent = mock.Mock()
        mock_agent.invoke.side_effect = RuntimeError("LLM API error")
        mock_build_agent.return_value = mock_agent

        result = get_progress_messages("TestBot", "A test bot")

        assert result == []

    @mock.patch("apps.help.agents.progress_messages.build_system_agent")
    def test_returns_empty_list_on_missing_structured_response(self, mock_build_agent):
        mock_agent = mock.Mock()
        mock_agent.invoke.return_value = {}
        mock_build_agent.return_value = mock_agent

        result = get_progress_messages("TestBot", "A test bot")

        assert result == []

    @mock.patch("apps.help.agents.progress_messages.build_system_agent")
    def test_excludes_description_when_empty(self, mock_build_agent):
        mock_agent = mock.Mock()
        mock_agent.invoke.return_value = {"structured_response": mock.Mock(messages=["Working..."])}
        mock_build_agent.return_value = mock_agent

        get_progress_messages("TestBot", "")

        call_args = mock_agent.invoke.call_args[0][0]
        user_message = call_args["messages"][0]["content"]
        assert "Description:" not in user_message


class TestGetProgressMessage:
    @mock.patch("apps.api.views.chat.get_progress_messages")
    def test_returns_first_message_and_caches_remainder(self, mock_get_messages):
        mock_get_messages.return_value = ["First", "Second", "Third"]

        result = get_progress_message("session-1", "TestBot", "desc")

        assert result == "First"
        # Second call should use cache, not call get_progress_messages again
        mock_get_messages.reset_mock()
        result2 = get_progress_message("session-1", "TestBot", "desc")
        assert result2 == "Second"
        mock_get_messages.assert_not_called()

    @mock.patch("apps.api.views.chat.get_progress_messages")
    def test_returns_none_when_no_messages(self, mock_get_messages):
        mock_get_messages.return_value = []

        result = get_progress_message("session-1", "TestBot", "desc")

        assert result is None

    @mock.patch("apps.api.views.chat.get_progress_messages")
    def test_deletes_cache_when_last_message_consumed(self, mock_get_messages):
        mock_get_messages.return_value = ["Only one"]

        result = get_progress_message("session-1", "TestBot", "desc")

        assert result == "Only one"
        # Next call should try to generate again since cache was deleted
        mock_get_messages.return_value = ["Fresh message"]
        result2 = get_progress_message("session-1", "TestBot", "desc")
        assert result2 == "Fresh message"
