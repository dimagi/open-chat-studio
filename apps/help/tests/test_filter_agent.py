from unittest import mock

import pytest
from pydantic import ValidationError

import apps.experiments.filters  # noqa: F401 — ensure filter subclasses are registered
from apps.help.agents.filter import FilterAgent, FilterInput, make_get_options_tool
from apps.web.dynamic_filters.base import ChoiceColumnFilter, MultiColumnFilter
from apps.web.dynamic_filters.column_filters import ParticipantFilter


class TestFilterAgentPrompt:
    def test_system_prompt_contains_schema(self):
        input_data = FilterInput(query="test", filter_slug="session", team_id=1)
        prompt = FilterAgent.get_system_prompt(input_data)
        # Should contain column names from ExperimentSessionFilter
        assert "participant" in prompt
        assert "tags" in prompt
        assert "channels" in prompt
        assert "state" in prompt

    def test_system_prompt_contains_operators(self):
        input_data = FilterInput(query="test", filter_slug="session", team_id=1)
        prompt = FilterAgent.get_system_prompt(input_data)
        assert "contains" in prompt
        assert "any of" in prompt

    def test_system_prompt_for_message_slug(self):
        input_data = FilterInput(query="test", filter_slug="message", team_id=1)
        prompt = FilterAgent.get_system_prompt(input_data)
        # ChatMessageFilter columns
        assert "tags" in prompt
        assert "last_message" in prompt
        assert "versions" in prompt
        # Should NOT have session-only columns
        assert '"participant"' not in prompt

    def test_unknown_slug_raises(self):
        input_data = FilterInput(query="test", filter_slug="nonexistent", team_id=1)
        with pytest.raises(ValueError, match="Unknown filter slug"):
            FilterAgent.get_system_prompt(input_data)

    def test_filter_input_requires_team_id(self):
        with pytest.raises(ValidationError):
            FilterInput(query="test", filter_slug="session")  # missing team_id


class TestMakeGetOptionsTool:
    """Tests for make_get_options_tool() — no DB required."""

    def _make_filter_class(self, filters):
        """Build a minimal MultiColumnFilter subclass with given filter list."""

        class FakeFilter(MultiColumnFilter):
            slug = "fake"
            date_range_column = ""

        FakeFilter.filters = filters
        return FakeFilter

    def _make_choice_filter(self, param, options):
        """Build a ChoiceColumnFilter that returns fixed options from prepare()."""
        f = ChoiceColumnFilter(query_param=param, label=param.title(), column=param)
        f.options = options
        return f

    def test_returns_options_for_choice_filter(self):
        choice_filter = self._make_choice_filter(
            "experiment",
            [{"id": 1, "label": "Alpha"}, {"id": 2, "label": "Beta"}],
        )
        filter_class = self._make_filter_class([choice_filter])
        team = mock.Mock()

        tool_fn = make_get_options_tool(filter_class, team)
        result = tool_fn.invoke({"param": "experiment"})

        assert result["total"] == 2
        assert result["returned"] == 2
        assert result["options"] == [{"id": 1, "label": "Alpha"}, {"id": 2, "label": "Beta"}]

    def test_normalizes_string_options(self):
        """String options (e.g. tags, channels) become {id, label} dicts."""
        choice_filter = self._make_choice_filter("tags", ["urgent", "billing"])
        filter_class = self._make_filter_class([choice_filter])
        team = mock.Mock()

        tool_fn = make_get_options_tool(filter_class, team)
        result = tool_fn.invoke({"param": "tags"})

        assert result["options"] == [
            {"id": "urgent", "label": "urgent"},
            {"id": "billing", "label": "billing"},
        ]

    def test_search_filters_by_label_case_insensitive(self):
        choice_filter = self._make_choice_filter(
            "experiment",
            [{"id": 1, "label": "Alpha Bot"}, {"id": 2, "label": "Beta Bot"}, {"id": 3, "label": "Gamma"}],
        )
        filter_class = self._make_filter_class([choice_filter])
        team = mock.Mock()

        tool_fn = make_get_options_tool(filter_class, team)
        result = tool_fn.invoke({"param": "experiment", "search": "bot"})

        assert result["total"] == 2
        assert result["returned"] == 2
        assert all("Bot" in opt["label"] for opt in result["options"])

    def test_limit_caps_results_but_total_reflects_full_count(self):
        options = [{"id": i, "label": f"Bot {i}"} for i in range(10)]
        choice_filter = self._make_choice_filter("experiment", options)
        filter_class = self._make_filter_class([choice_filter])
        team = mock.Mock()

        tool_fn = make_get_options_tool(filter_class, team)
        result = tool_fn.invoke({"param": "experiment", "limit": 3})

        assert result["total"] == 10
        assert result["returned"] == 3
        assert len(result["options"]) == 3

    def test_error_for_unknown_param(self):
        filter_class = self._make_filter_class([])
        team = mock.Mock()

        tool_fn = make_get_options_tool(filter_class, team)
        result = tool_fn.invoke({"param": "nonexistent"})

        assert "error" in result
        assert "nonexistent" in result["error"]

    def test_error_for_non_choice_filter(self):
        string_filter = ParticipantFilter()
        filter_class = self._make_filter_class([string_filter])
        team = mock.Mock()

        tool_fn = make_get_options_tool(filter_class, team)
        result = tool_fn.invoke({"param": "participant"})

        assert "error" in result

    def test_prepare_is_called_with_team(self):
        """prepare(team) must be called so DB-backed filters load options."""
        choice_filter = mock.Mock(spec=ChoiceColumnFilter)
        choice_filter.query_param = "experiment"
        choice_filter.options = [{"id": 99, "label": "Mocked"}]
        # model_copy(deep=True) should return the mock itself for simplicity
        choice_filter.model_copy.return_value = choice_filter

        filter_class = self._make_filter_class([choice_filter])
        team = mock.Mock()

        tool_fn = make_get_options_tool(filter_class, team)
        tool_fn.invoke({"param": "experiment"})

        choice_filter.prepare.assert_called_once_with(team)
