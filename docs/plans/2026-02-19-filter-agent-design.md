# Filter Agent Design

## Goal

Create a filter agent that converts natural language queries into structured `ColumnFilterData` filters. Must work across multiple filter configurations (ExperimentSessionFilter, ChatMessageFilter, etc.) using a single prompt template with an auto-derived schema.

## Approach

**Schema-in-prompt**: A static prompt template with a placeholder for the column schema. At runtime, the schema is auto-derived by introspecting the `ColumnFilter` instances on the target `MultiColumnFilter` subclass, serialized as JSON, and injected into the prompt.

## Data Model Changes

### ColumnFilter (apps/web/dynamic_filters/base.py)

Add a `description: str = ""` field. Each filter subclass provides a human-readable description for the AI agent (e.g. "Filter by participant name or identifier").

### MultiColumnFilter (apps/web/dynamic_filters/base.py)

Add a `slug: ClassVar[str] = ""` attribute. Each subclass declares a slug (e.g. `"session"`, `"message"`) used to look up the filter class at runtime.

### Schema extraction functions (apps/web/dynamic_filters/base.py)

- `get_filter_schema(filter_class)` — introspects `filter_class.filters` to extract `query_param`, `label`, `type`, `description`, and `operators` for each column. No DB calls.
- `get_filter_registry()` — builds `{slug: class}` dict from `MultiColumnFilter.__subclasses__()`.

## FilterAgent Changes

### FilterInput

```python
class FilterInput(BaseModel):
    query: str
    filter_slug: str  # e.g. "session", "message"
```

### get_system_prompt

Looks up filter class by slug, extracts schema via `get_filter_schema()`, injects into a markdown prompt template loaded from `apps/help/filter_system_prompt.md`.

### Prompt template (apps/help/filter_system_prompt.md)

Contains:
1. Role definition
2. Schema placeholder (`{schema}`)
3. Value encoding rules (plain strings, JSON arrays, relative dates, version format)
4. Output instructions (produce ColumnFilterData with column, operator, value)
5. Key rules (only use schema columns/operators, one filter per column, infer best operator)

### FilterOutput

Unchanged — `filters: list[ColumnFilterData]` with structured output via `response_format`.

## Description Values

| Filter | Description |
|--------|-------------|
| ParticipantFilter | Filter by participant name or identifier |
| ExperimentFilter | Filter by chatbot (experiment) name |
| StatusFilter | Filter by session status (e.g. active, complete) |
| RemoteIdFilter | Filter by participant's remote/external ID |
| TimestampFilter | Set per-instance via constructor arg |
| ChatMessageTagsFilter | Filter by tags on sessions or messages |
| MessageTagsFilter | Filter by tags on messages |
| VersionsFilter | Filter by chatbot version (e.g. v1, v2) |
| MessageVersionsFilter | Filter by message version |
| ChannelsFilter | Filter by messaging platform/channel |

## Eval Updates

- Add `filter_slug: "session"` to all existing fixture cases
- Add at least one `filter_slug: "message"` test case for ChatMessageFilter

## Files Changed

| File | Change |
|------|--------|
| apps/web/dynamic_filters/base.py | Add description to ColumnFilter, slug to MultiColumnFilter, add get_filter_schema() and get_filter_registry() |
| apps/web/dynamic_filters/column_filters.py | Add description to each filter |
| apps/experiments/filters.py | Add slug to filter classes, add description to filter instances |
| apps/help/agents/filter.py | Add filter_slug to FilterInput, implement get_system_prompt |
| apps/help/filter_system_prompt.md | New: prompt template |
| apps/help/evals/fixtures/filter.yml | Add filter_slug to all cases, add message case |
