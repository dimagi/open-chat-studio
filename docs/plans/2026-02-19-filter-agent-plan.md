# Filter Agent Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement a filter agent that converts natural language queries into structured `ColumnFilterData` filters, working across multiple filter configurations via auto-derived schemas.

**Architecture:** A single prompt template receives a JSON schema auto-derived from `MultiColumnFilter` subclasses. Each subclass declares a `slug` ClassVar. `ColumnFilter` gains a `description` field. The agent looks up the filter class by slug, extracts the schema, and injects it into the prompt.

**Tech Stack:** Django, Pydantic, LangGraph (via `build_system_agent`), pytest

**Design doc:** `docs/plans/2026-02-19-filter-agent-design.md`

---

### Task 1: Add `description` field to ColumnFilter and `slug` to MultiColumnFilter

**Files:**
- Modify: `apps/web/dynamic_filters/base.py:102` (ColumnFilter class)
- Modify: `apps/web/dynamic_filters/base.py:66` (MultiColumnFilter class)
- Test: `apps/web/tests/test_dynamic_filters.py` (new file)

**Step 1: Write the failing tests**

Create `apps/web/tests/test_dynamic_filters.py`:

```python
from apps.web.dynamic_filters.base import (
    FIELD_TYPE_FILTERS,
    ColumnFilter,
    MultiColumnFilter,
    StringColumnFilter,
    get_filter_registry,
    get_filter_schema,
)


class TestColumnFilterDescription:
    def test_default_description_is_empty(self):
        f = ColumnFilter(query_param="test", label="Test", type="string")
        assert f.description == ""

    def test_description_can_be_set(self):
        f = ColumnFilter(query_param="test", label="Test", type="string", description="A test filter")
        assert f.description == "A test filter"


class _TestFilter(MultiColumnFilter):
    slug = "test_slug"
    filters = [
        StringColumnFilter(
            query_param="name",
            label="Name",
            columns=["name_col"],
            description="Filter by name",
        ),
    ]


class TestGetFilterSchema:
    def test_extracts_schema(self):
        schema = get_filter_schema(_TestFilter)
        assert "name" in schema
        assert schema["name"]["label"] == "Name"
        assert schema["name"]["type"] == "string"
        assert schema["name"]["description"] == "Filter by name"
        assert schema["name"]["operators"] == [op.value for op in FIELD_TYPE_FILTERS["string"]]

    def test_schema_keys_are_query_params(self):
        schema = get_filter_schema(_TestFilter)
        assert list(schema.keys()) == ["name"]


class TestGetFilterRegistry:
    def test_includes_slugged_subclasses(self):
        registry = get_filter_registry()
        assert "test_slug" in registry
        assert registry["test_slug"] is _TestFilter

    def test_excludes_unslugged_subclasses(self):
        registry = get_filter_registry()
        for slug, cls in registry.items():
            assert slug != ""
```

**Step 2: Run tests to verify they fail**

Run: `pytest apps/web/tests/test_dynamic_filters.py -v`
Expected: ImportError for `get_filter_schema`, `get_filter_registry`; AttributeError for `description`

**Step 3: Implement the changes**

In `apps/web/dynamic_filters/base.py`, add to `ColumnFilter`:

```python
class ColumnFilter(BaseModel):
    query_param: str
    label: str
    type: TYPE_ANNOTATION
    column: str = None
    description: str = ""  # NEW
```

Add `slug` to `MultiColumnFilter`:

```python
class MultiColumnFilter:
    slug: ClassVar[str] = ""
    filters: ClassVar[Sequence[ColumnFilter]]
```

Add the two functions after `MultiColumnFilter`:

```python
def get_filter_schema(filter_class: type[MultiColumnFilter]) -> dict[str, dict]:
    """Extract static schema from a MultiColumnFilter for use in AI prompts.

    Returns a dict keyed by query_param with label, type, description, and operators.
    Does not call prepare() — no DB access needed.
    """
    schema = {}
    for f in filter_class.filters:
        schema[f.query_param] = {
            "label": f.label,
            "type": f.type,
            "description": f.description,
            "operators": [op.value for op in FIELD_TYPE_FILTERS[f.type]],
        }
    return schema


def get_filter_registry() -> dict[str, type[MultiColumnFilter]]:
    """Build registry of slug -> MultiColumnFilter class from all subclasses."""
    return {
        cls.slug: cls
        for cls in MultiColumnFilter.__subclasses__()
        if getattr(cls, "slug", "")
    }
```

**Step 4: Run tests to verify they pass**

Run: `pytest apps/web/tests/test_dynamic_filters.py -v`
Expected: All PASS

**Step 5: Lint and type check**

Run: `ruff check apps/web/dynamic_filters/base.py apps/web/tests/test_dynamic_filters.py --fix && ruff format apps/web/dynamic_filters/base.py apps/web/tests/test_dynamic_filters.py`

**Step 6: Commit**

```
git add apps/web/dynamic_filters/base.py apps/web/tests/test_dynamic_filters.py
git commit -m "feat: add description to ColumnFilter, slug to MultiColumnFilter, schema extraction"
```

---

### Task 2: Add descriptions to filter subclasses in column_filters.py

**Files:**
- Modify: `apps/web/dynamic_filters/column_filters.py`

**Step 1: Add descriptions**

```python
class ParticipantFilter(StringColumnFilter):
    query_param: str = "participant"
    columns: list[str] = ["participant__identifier", "participant__name"]
    label: str = "Participant"
    description: str = "Filter by participant name or identifier"


class ExperimentFilter(ChoiceColumnFilter):
    query_param: str = "experiment"
    column: str = "experiment_id"
    label: str = "Chatbot"
    description: str = "Filter by chatbot (experiment) name"

    # ... rest unchanged


class StatusFilter(ChoiceColumnFilter):
    column: str = "status"
    label: str = "Status"
    options: list[str] = SessionStatus.for_chatbots()
    description: str = "Filter by session status (e.g. active, complete)"


class RemoteIdFilter(ChoiceColumnFilter):
    query_param: str = "remote_id"
    column: str = "participant__remote_id"
    label: str = "Remote ID"
    description: str = "Filter by participant's remote/external ID"


class TimestampFilter(ColumnFilter):
    type: str = TYPE_TIMESTAMP
    options: list[dict[str, str]] = DATE_RANGE_OPTIONS
    description: str = "Filter by date/time"
    # Note: instances in ExperimentSessionFilter override this via constructor
```

**Step 2: Lint**

Run: `ruff check apps/web/dynamic_filters/column_filters.py --fix && ruff format apps/web/dynamic_filters/column_filters.py`

**Step 3: Run existing tests**

Run: `pytest apps/web/tests/ apps/experiments/tests/test_message_filters.py -v`
Expected: All PASS (description is additive, no behavior change)

**Step 4: Commit**

```
git add apps/web/dynamic_filters/column_filters.py
git commit -m "feat: add descriptions to column filter subclasses"
```

---

### Task 3: Add slugs and descriptions to filter configs in experiments/filters.py

**Files:**
- Modify: `apps/experiments/filters.py`

**Step 1: Add slugs and descriptions**

```python
class ChatMessageTagsFilter(ChoiceColumnFilter):
    query_param: str = "tags"
    label: str = "Tags"
    type: str = TYPE_CHOICE
    description: str = "Filter by tags on sessions or messages"
    # ... rest unchanged


class MessageTagsFilter(ChatMessageTagsFilter):
    """Simple tags filter for messages - works directly on message tags."""
    description: str = "Filter by tags on messages"
    # ... rest unchanged


class VersionsFilter(ChoiceColumnFilter):
    query_param: str = "versions"
    label: str = "Versions"
    description: str = "Filter by chatbot version (e.g. v1, v2)"
    # ... rest unchanged


class MessageVersionsFilter(VersionsFilter):
    """Versions filter for messages - works directly on message version tags."""
    description: str = "Filter by message version"
    # ... rest unchanged


class ChannelsFilter(ChoiceColumnFilter):
    query_param: str = "channels"
    label: str = "Channels"
    column: str = "platform"
    description: str = "Filter by messaging platform/channel"
    # ... rest unchanged


class ExperimentSessionFilter(MultiColumnFilter):
    slug: ClassVar[str] = "session"
    filters: ClassVar[Sequence[ColumnFilter]] = [
        ParticipantFilter(),
        TimestampFilter(label="Last Message", column="last_activity_at", query_param="last_message",
                        description="Filter by last message time"),
        TimestampFilter(label="First Message", column="first_activity_at", query_param="first_message",
                        description="Filter by first message time"),
        TimestampFilter(label="Message Date", column="chat__messages__created_at", query_param="message_date",
                        description="Filter by message date"),
        ChatMessageTagsFilter(),
        VersionsFilter(),
        ChannelsFilter(),
        ExperimentFilter(),
        StatusFilter(query_param="state"),
        RemoteIdFilter(),
    ]


class ChatMessageFilter(MultiColumnFilter):
    slug: ClassVar[str] = "message"
    filters: ClassVar[Sequence[ColumnFilter]] = [
        MessageTagsFilter(),
        TimestampFilter(label="Message Time", column="created_at", query_param="last_message",
                        description="Filter by message time"),
        MessageVersionsFilter(),
    ]
```

**Step 2: Write a test for schema extraction from real filter classes**

Add to `apps/web/tests/test_dynamic_filters.py`:

```python
class TestExperimentSessionFilterSchema:
    def test_schema_has_all_columns(self):
        from apps.experiments.filters import ExperimentSessionFilter

        schema = get_filter_schema(ExperimentSessionFilter)
        expected_keys = {
            "participant", "last_message", "first_message", "message_date",
            "tags", "versions", "channels", "experiment", "state", "remote_id",
        }
        assert set(schema.keys()) == expected_keys

    def test_all_columns_have_descriptions(self):
        from apps.experiments.filters import ExperimentSessionFilter

        schema = get_filter_schema(ExperimentSessionFilter)
        for key, col in schema.items():
            assert col["description"], f"Column {key!r} has no description"


class TestChatMessageFilterSchema:
    def test_schema_has_all_columns(self):
        from apps.experiments.filters import ChatMessageFilter

        schema = get_filter_schema(ChatMessageFilter)
        expected_keys = {"tags", "last_message", "versions"}
        assert set(schema.keys()) == expected_keys
```

**Step 3: Run tests**

Run: `pytest apps/web/tests/test_dynamic_filters.py -v`
Expected: All PASS

**Step 4: Lint**

Run: `ruff check apps/experiments/filters.py apps/web/tests/test_dynamic_filters.py --fix && ruff format apps/experiments/filters.py apps/web/tests/test_dynamic_filters.py`

**Step 5: Commit**

```
git add apps/experiments/filters.py apps/web/tests/test_dynamic_filters.py
git commit -m "feat: add slugs and descriptions to ExperimentSessionFilter and ChatMessageFilter"
```

---

### Task 4: Create the system prompt template

**Files:**
- Create: `apps/help/filter_system_prompt.md`

**Step 1: Write the prompt**

Create `apps/help/filter_system_prompt.md`:

```markdown
You are a filter assistant. Your job is to convert a natural language query into a list of structured filters.

## Available Filters

The following filter columns are available. You may ONLY use columns and operators listed here.

{schema}

## Output Format

Produce a list of filters. Each filter has three fields:
- `column`: The column identifier (must be one of the keys from the schema above)
- `operator`: The operator to apply (must be valid for that column's type)
- `value`: The filter value as a string

## Value Encoding Rules

- **Single string values**: Use the plain string. Example: `"john"`
- **Multiple values** (for "any of", "all of", "excludes" operators): Use a JSON array as a string. Example: `["WhatsApp", "Telegram"]`
- **Timestamp ranges**: Use relative duration strings: `"1h"` (1 hour), `"1d"` (1 day), `"7d"` (7 days), `"15d"` (15 days), `"30d"` (30 days), `"90d"` (3 months), `"365d"` (1 year). Use these with the `range` operator.
- **Specific dates**: Use ISO 8601 format with the `on`, `before`, or `after` operators.
- **Version values**: Use the version name as the user says it, e.g. `"v5"`, `"v6"`. For multiple versions use a JSON array: `["v5", "v6"]`

## Operator Selection Guide

- User says "starts with X" → use `starts with` operator
- User says "containing X" or "with X" (for text fields) → use `contains` operator
- User says "from X or Y" or lists multiple items → use `any of` operator with JSON array value
- User says "with both X and Y" or "all of" → use `all of` operator with JSON array value
- User says "without X" or "excluding X" → use `excludes` operator
- User mentions a time period like "last week", "last 30 days", "last hour" → use `range` operator with the closest duration value
- User says "active", "completed" etc. for status → use `any of` operator with JSON array value

## Rules

1. Only use columns from the schema. If the query mentions something not in the schema, ignore that part.
2. Only use operators that are valid for the column's type.
3. Each column should appear at most once in the output.
4. Produce the minimum number of filters needed to satisfy the query.
5. Use the `range` operator for relative time expressions (e.g. "last week" → `7d`, "last 3 months" → `90d`).
```

**Step 2: Commit**

```
git add apps/help/filter_system_prompt.md
git commit -m "feat: add filter agent system prompt template"
```

---

### Task 5: Implement FilterAgent with schema injection

**Files:**
- Modify: `apps/help/agents/filter.py`

**Step 1: Write a unit test for prompt construction**

Add `apps/help/tests/test_filter_agent.py`:

```python
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
        import pytest
        input_data = FilterInput(query="test", filter_slug="nonexistent")
        with pytest.raises(KeyError):
            FilterAgent.get_system_prompt(input_data)
```

**Step 2: Run tests to verify they fail**

Run: `pytest apps/help/tests/test_filter_agent.py -v`
Expected: FAIL (prompt is still "TODO")

**Step 3: Implement FilterAgent**

Update `apps/help/agents/filter.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel

from apps.help.base import BaseHelpAgent
from apps.help.registry import register_agent
from apps.web.dynamic_filters.datastructures import ColumnFilterData

_system_prompt = None


def _get_system_prompt():
    global _system_prompt
    if _system_prompt is None:
        _system_prompt = (Path(__file__).parent.parent / "filter_system_prompt.md").read_text()
    return _system_prompt


class FilterInput(BaseModel):
    query: str
    filter_slug: str


class FilterOutput(BaseModel):
    filters: list[ColumnFilterData]


@register_agent
class FilterAgent(BaseHelpAgent[FilterInput, FilterOutput]):
    name: ClassVar[str] = "filter"
    mode: ClassVar[Literal["high", "low"]] = "low"

    @classmethod
    def get_system_prompt(cls, input: FilterInput) -> str:
        from apps.web.dynamic_filters.base import get_filter_registry, get_filter_schema

        registry = get_filter_registry()
        filter_class = registry[input.filter_slug]
        schema = get_filter_schema(filter_class)
        template = _get_system_prompt()
        return template.format(schema=json.dumps(schema, indent=2))

    @classmethod
    def get_user_message(cls, input: FilterInput) -> str:
        return input.query
```

**Step 4: Run tests to verify they pass**

Run: `pytest apps/help/tests/test_filter_agent.py -v`
Expected: All PASS

**Step 5: Lint**

Run: `ruff check apps/help/agents/filter.py apps/help/tests/test_filter_agent.py --fix && ruff format apps/help/agents/filter.py apps/help/tests/test_filter_agent.py`

**Step 6: Commit**

```
git add apps/help/agents/filter.py apps/help/tests/test_filter_agent.py
git commit -m "feat: implement FilterAgent with schema-in-prompt"
```

---

### Task 6: Update eval fixtures

**Files:**
- Modify: `apps/help/evals/fixtures/filter.yml`

**Step 1: Add `filter_slug: "session"` to all existing cases and add a message case**

Update every existing case to include `filter_slug: "session"` in input. Add one new case:

```yaml
- id: message_tagged_recent
  input:
    query: "messages tagged 'important' from the last day"
    filter_slug: "message"
  checks:
    - type: count
      expected: 2
    - type: filter_params
      expected: ["last_message", "tags"]
```

**Step 2: Run non-eval tests to confirm nothing breaks**

Run: `pytest apps/help/tests/test_filter_agent.py apps/help/evals/test_checks.py apps/web/tests/test_dynamic_filters.py -v`
Expected: All PASS

**Step 3: Lint**

Run: `ruff check apps/help/evals/ --fix`

**Step 4: Commit**

```
git add apps/help/evals/fixtures/filter.yml
git commit -m "feat: update filter eval fixtures with filter_slug"
```

---

### Task 7: Run full test suite and final verification

**Step 1: Run all related unit tests**

Run: `pytest apps/web/tests/test_dynamic_filters.py apps/help/tests/test_filter_agent.py apps/help/evals/test_checks.py apps/web/tests/test_datastructures.py apps/experiments/tests/test_message_filters.py -v`
Expected: All PASS

**Step 2: Lint all modified files**

Run: `ruff check apps/web/dynamic_filters/ apps/experiments/filters.py apps/help/agents/filter.py apps/help/tests/ --fix && ruff format apps/web/dynamic_filters/ apps/experiments/filters.py apps/help/agents/filter.py apps/help/tests/`

**Step 3: Type check**

Run: `ty check apps/web/dynamic_filters/ apps/help/agents/filter.py`
