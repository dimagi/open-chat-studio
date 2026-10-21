from unittest import mock

import pytest
import time_machine
from django.core.cache import cache

from apps.api.progress_messages import get_progress_message, get_progress_messages
from apps.api.tasks import generate_progress_messages_task


@pytest.fixture(autouse=True)
def _clear_cache():
    yield
    cache.clear()


class TestGetProgressMessages:
    @mock.patch("apps.help.base.build_system_agent")
    def test_returns_messages_on_success(self, mock_build_agent):
        mock_agent = mock.Mock()
        mock_agent.invoke.return_value = {"structured_response": mock.Mock(messages=["Thinking...", "Almost there..."])}
        mock_build_agent.return_value = mock_agent

        result = get_progress_messages(chatbot_name="TestBot", chatbot_description="A test bot")

        assert result == ["Thinking...", "Almost there..."]

    @mock.patch("apps.help.base.build_system_agent")
    def test_returns_empty_list_on_agent_build_failure(self, mock_build_agent):
        mock_build_agent.side_effect = Exception("no system agent models configured")

        result = get_progress_messages(chatbot_name="TestBot", chatbot_description="A test bot")

        assert result == []

    @mock.patch("apps.help.base.build_system_agent")
    def test_returns_empty_list_on_invoke_failure(self, mock_build_agent):
        mock_agent = mock.Mock()
        mock_agent.invoke.side_effect = RuntimeError("LLM API error")
        mock_build_agent.return_value = mock_agent

        result = get_progress_messages(chatbot_name="TestBot", chatbot_description="A test bot")

        assert result == []

    @mock.patch("apps.help.base.build_system_agent")
    def test_returns_empty_list_on_missing_structured_response(self, mock_build_agent):
        mock_agent = mock.Mock()
        mock_agent.invoke.return_value = {}
        mock_build_agent.return_value = mock_agent

        result = get_progress_messages(chatbot_name="TestBot", chatbot_description="A test bot")

        assert result == []

    @mock.patch("apps.help.base.build_system_agent")
    def test_excludes_description_when_empty(self, mock_build_agent):
        mock_agent = mock.Mock()
        mock_agent.invoke.return_value = {"structured_response": mock.Mock(messages=["Working..."])}
        mock_build_agent.return_value = mock_agent

        get_progress_messages(chatbot_name="TestBot", chatbot_description="")

        call_args = mock_agent.invoke.call_args[0][0]
        user_message = call_args["messages"][0]["content"]
        assert "Description:" not in user_message


class TestGetProgressMessage:
    @mock.patch("apps.api.tasks.generate_progress_messages_task")
    @mock.patch("apps.api.progress_messages.ProgressMessagesAgent")
    def test_cache_miss_queues_generation_without_calling_agent(self, mock_agent, mock_task):
        assert get_progress_message("session-1", "TestBot", "desc") is None

        mock_agent.assert_not_called()
        mock_task.delay.assert_called_once_with(
            session_id="session-1", chatbot_name="TestBot", chatbot_description="desc"
        )

    @mock.patch("apps.api.tasks.generate_progress_messages_task")
    def test_repeated_cache_misses_queue_generation_once(self, mock_task):
        get_progress_message("session-1", "TestBot", "desc")
        get_progress_message("session-1", "TestBot", "desc")

        mock_task.delay.assert_called_once()

    def test_read_refreshes_cache_ttl(self):
        with time_machine.travel("2026-01-01 00:00", tick=False) as traveller:
            cache.set("progress_messages:session-1", ["First", "Second"], 10)
            get_progress_message("session-1", "TestBot", "desc")

            traveller.shift(60)

            assert cache.get("progress_messages:session-1") == ["First", "Second"]

    def test_returns_cached_messages_in_order(self):
        cache.set("progress_messages:session-1", ["First", "Second", "Third"])

        results = [get_progress_message("session-1", "TestBot", "desc") for _ in range(3)]

        assert results == ["First", "Second", "Third"]

    def test_loops_back_to_first_message_after_last(self):
        cache.set("progress_messages:session-1", ["First", "Second"])

        results = [get_progress_message("session-1", "TestBot", "desc") for _ in range(5)]

        assert results == ["First", "Second", "First", "Second", "First"]

    def test_throttle_key_repeats_message_within_window(self):
        cache.set("progress_messages:session-1", ["First", "Second"])

        first = get_progress_message("session-1", "TestBot", "desc", throttle_key="task-1")
        second = get_progress_message("session-1", "TestBot", "desc", throttle_key="task-1")

        assert first == second == "First"


class TestGenerateProgressMessagesTask:
    @mock.patch("apps.api.tasks.get_progress_messages")
    def test_caches_generated_messages(self, mock_get_messages):
        mock_get_messages.return_value = ["Thinking...", "Working..."]

        generate_progress_messages_task(session_id="session-1", chatbot_name="TestBot", chatbot_description="desc")

        mock_get_messages.assert_called_once_with(chatbot_name="TestBot", chatbot_description="desc")
        assert cache.get("progress_messages:session-1") == ["Thinking...", "Working..."]

    @mock.patch("apps.api.tasks.get_progress_messages")
    def test_caches_nothing_when_generation_fails(self, mock_get_messages):
        mock_get_messages.return_value = []

        generate_progress_messages_task(session_id="session-1", chatbot_name="TestBot", chatbot_description="desc")

        assert cache.get("progress_messages:session-1") is None
