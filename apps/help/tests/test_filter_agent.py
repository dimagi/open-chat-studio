import pytest

import apps.experiments.filters  # noqa: F401 — ensure filter subclasses are registered
from apps.help.agents.filter import FilterAgent, FilterInput


class TestFilterAgentPrompt:
    def test_system_prompt_contains_schema(self):
        input_data = FilterInput(query="test", filter_slug="session")
        prompt = FilterAgent.get_system_prompt(input_data)
        # Should contain column names from ExperimentSessionFilter
        assert "participant" in prompt
        assert "tags" in prompt
        assert "channels" in prompt
        assert "state" in prompt

    def test_system_prompt_contains_operators(self):
        input_data = FilterInput(query="test", filter_slug="session")
        prompt = FilterAgent.get_system_prompt(input_data)
        assert "contains" in prompt
        assert "any of" in prompt

    def test_system_prompt_for_message_slug(self):
        input_data = FilterInput(query="test", filter_slug="message")
        prompt = FilterAgent.get_system_prompt(input_data)
        # ChatMessageFilter columns
        assert "tags" in prompt
        assert "last_message" in prompt
        assert "versions" in prompt
        # Should NOT have session-only columns
        assert '"participant"' not in prompt

    def test_unknown_slug_raises(self):
        input_data = FilterInput(query="test", filter_slug="nonexistent")
        with pytest.raises(KeyError):
            FilterAgent.get_system_prompt(input_data)
