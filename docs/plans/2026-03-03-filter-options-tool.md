# Filter Options Tool Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a `get_filter_options` LangChain tool to `FilterAgent` so the agent can look up valid option values (e.g. chatbot names, tags, channel names) at runtime instead of guessing them.

**Architecture:** `FilterInput` gains a `team_id` field (injected server-side by the view). A factory function `make_get_options_tool(filter_class, team)` returns a LangChain tool that calls `ChoiceColumnFilter.prepare(team)` on demand and returns searchable, capped results. `FilterAgent` overrides `run()` to build the tool and pass it to `build_system_agent`.

**Tech Stack:** Python 3.13, Django, Pydantic v2, LangChain (`langchain_core.tools.tool` decorator)

---

### Task 1: Add `team_id` to `FilterInput`

**Files:**
- Modify: `apps/help/agents/filter.py:20-23`
- Modify: `apps/help/tests/test_filter_agent.py` (update all `FilterInput(...)` calls)
- Modify: `apps/help/tests/test_help.py` (update `test_successful_filter_agent_call`)

**Step 1: Write failing tests**

In `apps/help/tests/test_filter_agent.py`, update all `FilterInput(...)` constructors to include `team_id=1` (it's now required):

```python
# Replace all FilterInput(...) calls — add team_id=1 to each
input_data = FilterInput(query="test", filter_slug="session", team_id=1)
```

Run: `uv run pytest apps/help/tests/test_filter_agent.py -v`
Expected: FAIL — `FilterInput` does not yet have `team_id` field, pydantic would still pass (it's missing but not failing validation).

Add a new explicit test for the field:

```python
def test_filter_input_requires_team_id():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        FilterInput(query="test", filter_slug="session")  # missing team_id
```

Run: `uv run pytest apps/help/tests/test_filter_agent.py::TestFilterAgentPrompt::test_filter_input_requires_team_id -v`
Expected: FAIL — `FilterInput` does not yet require `team_id`.

**Step 2: Add `team_id` to `FilterInput`**

In `apps/help/agents/filter.py`, change:

```python
class FilterInput(BaseModel):
    query: str
    filter_slug: str
```

to:

```python
class FilterInput(BaseModel):
    query: str
    filter_slug: str
    team_id: int
```

**Step 3: Run tests**

Run: `uv run pytest apps/help/tests/test_filter_agent.py -v`
Expected: PASS (all prompt tests still pass, new test passes)

Also run: `uv run pytest apps/help/tests/test_help.py -v`
Expected: Some failures in `TestRunAgentView::test_successful_filter_agent_call` because the body no longer has `team_id`. Note which tests fail — they will be fixed in Task 4.

**Step 4: Update existing filter agent prompt tests to include `team_id`**

In `apps/help/tests/test_filter_agent.py`, update all `FilterInput(...)` constructors:

```python
# test_system_prompt_contains_schema
input_data = FilterInput(query="test", filter_slug="session", team_id=1)

# test_system_prompt_contains_operators
input_data = FilterInput(query="test", filter_slug="session", team_id=1)

# test_system_prompt_for_message_slug
input_data = FilterInput(query="test", filter_slug="message", team_id=1)

# test_unknown_slug_raises
input_data = FilterInput(query="test", filter_slug="nonexistent", team_id=1)
```

Run: `uv run pytest apps/help/tests/test_filter_agent.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add apps/help/agents/filter.py apps/help/tests/test_filter_agent.py
git commit -m "feat: add team_id to FilterInput"
```

---

### Task 2: Implement `make_get_options_tool()`

**Files:**
- Modify: `apps/help/agents/filter.py` (add function after imports, before `FilterInput`)
- Test: `apps/help/tests/test_filter_agent.py` (new `TestMakeGetOptionsTool` class)

**Step 1: Write failing tests**

Add a new test class to `apps/help/tests/test_filter_agent.py`:

```python
import apps.experiments.filters  # noqa: F401 — already at top of file
from apps.help.agents.filter import FilterAgent, FilterInput, make_get_options_tool
from apps.web.dynamic_filters.base import ChoiceColumnFilter, ColumnFilter, MultiColumnFilter, TYPE_CHOICE
from unittest import mock


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
        from apps.web.dynamic_filters.column_filters import ParticipantFilter
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
```

Run: `uv run pytest apps/help/tests/test_filter_agent.py::TestMakeGetOptionsTool -v`
Expected: FAIL — `make_get_options_tool` does not exist yet.

**Step 2: Implement `make_get_options_tool()`**

In `apps/help/agents/filter.py`, add imports and the function. Final file state for the top section:

```python
from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import ClassVar, Literal

from langchain_core.tools import tool
from pydantic import BaseModel

from apps.help.base import BaseHelpAgent
from apps.help.registry import register_agent
from apps.web.dynamic_filters.datastructures import ColumnFilterData


@functools.cache
def _get_system_prompt():
    return (Path(__file__).parent.parent / "filter_system_prompt.md").read_text()


def make_get_options_tool(filter_class, team):
    """Return a LangChain tool that fetches options for a choice filter parameter.

    The tool is closed over filter_class and team so it can call prepare(team)
    on the appropriate ColumnFilter instance without needing extra arguments.
    """
    from apps.web.dynamic_filters.base import ChoiceColumnFilter

    @tool
    def get_filter_options(param: str, search: str = "", limit: int = 50) -> dict:
        """Get available options for a choice or exclusive_choice filter parameter.

        Call this tool before using a choice/exclusive_choice filter to look up
        valid option values. Use the returned option IDs (not labels) as filter values.

        Args:
            param: The filter query_param name (e.g. 'experiment', 'tags', 'channels').
            search: Optional case-insensitive substring to filter options by label.
            limit: Maximum number of options to return (default 50).

        Returns:
            Dict with 'options' (list of {id, label}), 'returned' (count returned),
            'total' (total matching before limit is applied).
            On error, returns {'error': '<message>'}.
        """
        filter_component = next(
            (f for f in filter_class.filters if f.query_param == param),
            None,
        )
        if filter_component is None:
            return {"error": f"No filter with param {param!r} found"}
        if not isinstance(filter_component, ChoiceColumnFilter):
            return {"error": f"Filter {param!r} does not have options (type: {filter_component.type})"}

        instance = filter_component.model_copy(deep=True)
        instance.prepare(team)

        normalized = []
        for opt in instance.options:
            if isinstance(opt, str):
                normalized.append({"id": opt, "label": opt})
            else:
                normalized.append(opt)

        if search:
            normalized = [opt for opt in normalized if search.lower() in opt["label"].lower()]

        total = len(normalized)
        limited = normalized[:limit]
        return {"options": limited, "returned": len(limited), "total": total}

    return get_filter_options
```

**Step 3: Run tests**

Run: `uv run pytest apps/help/tests/test_filter_agent.py::TestMakeGetOptionsTool -v`
Expected: PASS (all 7 tests)

**Step 4: Lint**

Run: `uv run ruff check apps/help/agents/filter.py --fix && uv run ruff format apps/help/agents/filter.py`

**Step 5: Commit**

```bash
git add apps/help/agents/filter.py apps/help/tests/test_filter_agent.py
git commit -m "feat: add make_get_options_tool for filter option lookup"
```

---

### Task 3: Override `FilterAgent.run()` to use the tool

**Files:**
- Modify: `apps/help/agents/filter.py` (add `run()` override to `FilterAgent`)
- Test: `apps/help/tests/test_filter_agent.py` (new `TestFilterAgentRun` class)

**Step 1: Write failing test**

Add a new test class to `apps/help/tests/test_filter_agent.py`:

```python
from apps.help.agent import build_system_agent as _build_system_agent  # just for the path


class TestFilterAgentRun:
    @mock.patch("apps.help.agents.filter.Team")
    @mock.patch("apps.help.agents.filter.build_system_agent")
    def test_run_passes_options_tool_to_agent(self, mock_build, mock_team_cls):
        """FilterAgent.run() should build one tool and pass it to build_system_agent."""
        stub_output = FilterOutput(filters=[])
        mock_agent = mock.Mock()
        mock_agent.invoke.return_value = {"structured_response": stub_output}
        mock_build.return_value = mock_agent

        mock_team_cls.objects.get.return_value = mock.Mock(id=1)

        import apps.experiments.filters  # noqa: F401
        agent = FilterAgent(input=FilterInput(query="active sessions", filter_slug="session", team_id=1))
        result = agent.run()

        assert result == stub_output
        call_kwargs = mock_build.call_args.kwargs
        assert "tools" in call_kwargs
        assert len(call_kwargs["tools"]) == 1
        assert call_kwargs["tools"][0].name == "get_filter_options"

    @mock.patch("apps.help.agents.filter.Team")
    @mock.patch("apps.help.agents.filter.build_system_agent")
    def test_run_fetches_team_by_team_id(self, mock_build, mock_team_cls):
        """FilterAgent.run() must look up the team from team_id."""
        stub_output = FilterOutput(filters=[])
        mock_agent = mock.Mock()
        mock_agent.invoke.return_value = {"structured_response": stub_output}
        mock_build.return_value = mock_agent
        mock_team_cls.objects.get.return_value = mock.Mock(id=42)

        import apps.experiments.filters  # noqa: F401
        agent = FilterAgent(input=FilterInput(query="test", filter_slug="session", team_id=42))
        agent.run()

        mock_team_cls.objects.get.assert_called_once_with(id=42)
```

Run: `uv run pytest apps/help/tests/test_filter_agent.py::TestFilterAgentRun -v`
Expected: FAIL — `FilterAgent` has no `run()` override and `build_system_agent` / `Team` are not imported in `filter.py`.

**Step 2: Override `run()` in `FilterAgent`**

In `apps/help/agents/filter.py`, add these two imports at the module level (after existing imports):

```python
from apps.help.agent import build_system_agent
from apps.teams.models import Team
```

Then add `run()` to `FilterAgent` (after `get_user_message`):

```python
    def run(self) -> FilterOutput:
        from apps.web.dynamic_filters.base import get_filter_registry

        registry = get_filter_registry()
        filter_class = registry[self.input.filter_slug]
        team = Team.objects.get(id=self.input.team_id)
        options_tool = make_get_options_tool(filter_class, team)
        agent = build_system_agent(
            self.mode,
            self.get_system_prompt(self.input),
            tools=[options_tool],
            response_format=self._get_output_type(),
        )
        response = agent.invoke(
            {"messages": [{"role": "user", "content": self.get_user_message(self.input)}]}
        )
        return self.parse_response(response)
```

**Step 3: Run tests**

Run: `uv run pytest apps/help/tests/test_filter_agent.py::TestFilterAgentRun -v`
Expected: PASS

**Step 4: Lint**

Run: `uv run ruff check apps/help/agents/filter.py --fix && uv run ruff format apps/help/agents/filter.py`

**Step 5: Commit**

```bash
git add apps/help/agents/filter.py apps/help/tests/test_filter_agent.py
git commit -m "feat: override FilterAgent.run() to register options tool"
```

---

### Task 4: Update views.py to inject `team_id`

**Files:**
- Modify: `apps/help/views.py`
- Modify: `apps/help/tests/test_help.py` (fix `TestRunAgentView::test_successful_filter_agent_call`)

**Step 1: Fix the broken view test first**

In `apps/help/tests/test_help.py`, update `test_successful_filter_agent_call`:

```python
@mock.patch("apps.help.agents.filter.Team")
@mock.patch("apps.help.agents.filter.build_system_agent")
def test_successful_filter_agent_call(self, mock_build, mock_team_cls):
    stub_output = FilterOutput(filters=[ColumnFilterData(column="state", operator="equals", value="setup")])
    mock_agent = mock.Mock()
    mock_agent.invoke.return_value = {"structured_response": stub_output}
    mock_build.return_value = mock_agent
    mock_team_cls.objects.get.return_value = mock.Mock(id=1)

    # Set request.team so the view can inject team_id
    request_factory = RequestFactory()
    inner = run_agent.__wrapped__.__wrapped__

    # Build request manually and attach team
    import json as _json
    request = request_factory.post(
        "/help/filter/",
        data=_json.dumps({"query": "active sessions", "filter_slug": "session"}),
        content_type="application/json",
    )
    request.team = mock.Mock(id=1)

    response = inner(request, team_slug="test-team", agent_name="filter")

    assert response.status_code == 200
    data = _json.loads(response.content)
    assert "response" in data
    assert "filters" in data["response"]
    assert data["response"]["filters"][0]["column"] == "state"
```

Note: `_make_request` helper doesn't set `request.team`, so we need to bypass it for this test. That's fine — this test replaces the old version.

Run: `uv run pytest apps/help/tests/test_help.py::TestRunAgentView::test_successful_filter_agent_call -v`
Expected: FAIL — view doesn't yet inject `team_id`.

**Step 2: Update `views.py` to inject `team_id`**

In `apps/help/views.py`, change:

```python
    try:
        body = json.loads(request.body)
        agent = agent_cls(input=body)
```

to:

```python
    try:
        body = json.loads(request.body)
        body["team_id"] = request.team.id
        agent = agent_cls(input=body)
```

**Step 3: Run tests**

Run: `uv run pytest apps/help/tests/test_help.py -v`
Expected: PASS (all view tests pass)

**Note:** Other agents (CodeGenerateAgent, ProgressMessagesAgent) don't have `team_id` in their input models, so Pydantic will silently ignore the extra field. Verify this is the case (Pydantic v2 ignores extra fields by default unless `model_config = ConfigDict(extra='forbid')`).

If any agent rejects the extra field, add `model_config = ConfigDict(extra='ignore')` to the relevant input models, or switch to using `body.pop("team_id")` after injecting into `FilterInput` specifically. Check first:

Run: `uv run pytest apps/help/ -v`
Expected: All tests pass.

**Step 4: Lint**

Run: `uv run ruff check apps/help/views.py apps/help/tests/test_help.py --fix`
Run: `uv run ruff format apps/help/views.py apps/help/tests/test_help.py`

**Step 5: Commit**

```bash
git add apps/help/views.py apps/help/tests/test_help.py
git commit -m "feat: inject team_id into agent input from request.team"
```

---

### Task 5: Update system prompt to document the tool

**Files:**
- Modify: `apps/help/filter_system_prompt.md`

**Step 1: Add tool documentation section**

Append to `apps/help/filter_system_prompt.md` (after the last `## Rules` section):

```markdown

## Available Tools

### `get_filter_options`

Use this tool to look up valid option values for `choice` or `exclusive_choice` filter types.

**When to call it:** Whenever the user's query refers to a choice filter (e.g. chatbot name, tags, channels) and you need to resolve a name or partial name to valid option values.

**Arguments:**
- `param` (required): The filter query_param from the schema (e.g. `"experiment"`, `"tags"`, `"channels"`)
- `search` (optional): A substring to narrow results (case-insensitive match on option label)
- `limit` (optional): Max options to return, default 50

**Returns:** `{"options": [{"id": ..., "label": ...}, ...], "returned": N, "total": M}`

**Rules for tool use:**
1. Always call the tool before using a choice filter value if you don't already know the exact option IDs.
2. Use the `search` parameter with the user's term to narrow results before selecting.
3. Use option **IDs** (not labels) as filter values in `ColumnFilterData`.
4. If `total > returned`, the list is truncated — refine your search to find the right option.
5. If the tool returns an error, skip that filter and proceed with remaining filters.

**Example:**
- User says "filter by chatbot Alpha Bot" → call `get_filter_options(param="experiment", search="Alpha Bot")` → get `[{"id": 42, "label": "Alpha Bot"}]` → use value `[42]`
- User says "filter by tag urgent" → call `get_filter_options(param="tags", search="urgent")` → get `[{"id": "urgent", "label": "urgent"}]` → use value `["urgent"]`
```

**Step 2: Clear the cached system prompt**

The system prompt is cached with `@functools.cache`. This is fine for production (cache is per-process), but note that tests that check the prompt content should re-import or clear the cache. No code change needed.

**Step 3: Verify the prompt is valid**

Run: `uv run pytest apps/help/tests/test_filter_agent.py -v`
Expected: All tests pass (prompt tests check for schema/operator content, not tool content).

**Step 4: Commit**

```bash
git add apps/help/filter_system_prompt.md
git commit -m "docs: add get_filter_options tool instructions to filter system prompt"
```

---

### Task 6: Run full test suite and type check

**Step 1: Run all help tests**

Run: `uv run pytest apps/help/ -v`
Expected: All tests pass.

**Step 2: Run filter-related tests**

Run: `uv run pytest apps/web/dynamic_filters/ -v`
Expected: All tests pass (no changes to this module).

**Step 3: Type check**

Run: `uv run ty check apps/help/`
Expected: No errors. If ty reports issues with the `@tool` decorator return type, add `# ty: ignore[...]` on that line only.

**Step 4: Lint**

Run: `uv run ruff check apps/help/ --fix && uv run ruff format apps/help/`

**Step 5: Final commit (if any lint fixes)**

```bash
git add -u
git commit -m "chore: lint fixes for filter options tool"
```

---

## Summary of Changed Files

| File | Change |
|------|--------|
| `apps/help/agents/filter.py` | Add `team_id` to `FilterInput`, add `make_get_options_tool()`, add `FilterAgent.run()` override, import `build_system_agent` and `Team` |
| `apps/help/views.py` | Inject `team_id` from `request.team.id` into body before creating agent |
| `apps/help/filter_system_prompt.md` | Add `## Available Tools` section documenting `get_filter_options` |
| `apps/help/tests/test_filter_agent.py` | Update `FilterInput(...)` calls to include `team_id=1`, add `TestMakeGetOptionsTool`, add `TestFilterAgentRun` |
| `apps/help/tests/test_help.py` | Update `test_successful_filter_agent_call` to mock `Team` and set `request.team` |

## Final `apps/help/agents/filter.py` Shape

```python
from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import ClassVar, Literal

from langchain_core.tools import tool
from pydantic import BaseModel

from apps.help.agent import build_system_agent
from apps.help.base import BaseHelpAgent
from apps.help.registry import register_agent
from apps.teams.models import Team
from apps.web.dynamic_filters.datastructures import ColumnFilterData


@functools.cache
def _get_system_prompt(): ...

def make_get_options_tool(filter_class, team): ...


class FilterInput(BaseModel):
    query: str
    filter_slug: str
    team_id: int


class FilterOutput(BaseModel):
    filters: list[ColumnFilterData]


@register_agent
class FilterAgent(BaseHelpAgent[FilterInput, FilterOutput]):
    name: ClassVar[str] = "filter"
    mode: ClassVar[Literal["high", "low"]] = "low"

    @classmethod
    def get_system_prompt(cls, input: FilterInput) -> str: ...

    @classmethod
    def get_user_message(cls, input: FilterInput) -> str: ...

    def run(self) -> FilterOutput: ...
```
