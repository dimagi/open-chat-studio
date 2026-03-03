# Filter Options Tool for FilterAgent

**Date:** 2026-03-03
**Status:** Approved

## Problem

The `FilterAgent` converts natural language queries to `ColumnFilterData` filter specs. For `choice` and `exclusive_choice` filter types (e.g. chatbot/experiment, tags, channels), the agent currently receives only the static schema — no knowledge of available options. This means it cannot resolve a name like "My Bot" to the correct ID, nor verify that a tag or channel exists.

## Solution

Add a `get_filter_options` LangChain tool that the agent can call at runtime to fetch and search available options for any filter parameter. The tool is closed over the filter class and team, providing access to the same options that `prepare(team)` would supply.

## Design

### 1. FilterInput gains `team_id`

```python
class FilterInput(BaseModel):
    query: str
    filter_slug: str
    team_id: int  # injected by the view from request.team.id
```

The frontend does NOT need to send `team_id` — the view injects it server-side.

### 2. View injects team_id

```python
# apps/help/views.py
body = json.loads(request.body)
body["team_id"] = request.team.id
agent = agent_cls(input=body)
```

### 3. make_get_options_tool()

A factory function in `apps/help/agents/filter.py` that returns a LangChain tool:

```
make_get_options_tool(filter_class, team) -> LangChain tool
```

The tool function signature:
```python
def get_filter_options(param: str, search: str = "", limit: int = 50) -> dict
```

**Behavior:**
- Finds the `ColumnFilter` with `query_param == param` from `filter_class.filters`
- Returns an error dict if param not found or not a `ChoiceColumnFilter`
- Deep-copies the filter, calls `prepare(team)` to populate options
- Normalizes options to `{id, label}` format:
  - `str` items → `{"id": item, "label": item}`
  - `dict` items → passed through (already have `id`/`label`)
- Filters by `search` (case-insensitive substring match on `label`)
- Caps results at `limit` (default 50)
- Returns `{"options": [...], "returned": N, "total": M}`

### 4. FilterAgent.run() override

```python
def run(self) -> FilterOutput:
    from apps.teams.models import Team
    registry = get_filter_registry()
    filter_class = registry[self.input.filter_slug]
    team = Team.objects.get(id=self.input.team_id)
    tool = make_get_options_tool(filter_class, team)
    agent = build_system_agent(
        self.mode,
        self.get_system_prompt(self.input),
        tools=[tool],
        response_format=FilterOutput,
    )
    response = agent.invoke(
        {"messages": [{"role": "user", "content": self.get_user_message(self.input)}]}
    )
    return self.parse_response(response)
```

### 5. System prompt update

Add a section to `filter_system_prompt.md` explaining:
- The tool is available for `choice` and `exclusive_choice` filter types
- Call it to look up available options before using them in filter values
- Use `search` param to narrow results when you have a partial name
- Option IDs (not labels) should be used as filter values

## Data Flow

```
User query → FilterAgent.run()
  → resolve Team from team_id
  → create get_filter_options tool (closure over filter_class + team)
  → agent.invoke(query)
    [LLM sees schema, recognises "experiment" is choice type]
    → calls get_filter_options(param="experiment", search="my bot")
    → tool: ExperimentFilter.prepare(team) → filter by "my bot" → [{id:42, label:"My Bot"}]
    → LLM uses id=42 → returns ColumnFilterData(column="experiment", operator="any of", value='[42]')
  → FilterOutput returned
```

## Edge Cases

- **Param not found**: tool returns `{"error": "No filter with param '<param>' found"}`
- **Not a choice filter**: tool returns `{"error": "Filter '<param>' does not have options"}`
- **Empty search**: returns all options (up to limit)
- **No options after filtering**: returns `{"options": [], "returned": 0, "total": 0}`
- **Static options** (e.g. StatusFilter): `prepare()` is a no-op, options already populated; tool works normally

## Testing

- Unit test for `make_get_options_tool()`:
  - Returns correct options for a choice filter
  - Search filtering works (case-insensitive)
  - Limit caps results, total reflects uncapped count
  - Error cases for unknown param and non-choice filter
- Integration test for `FilterAgent` with mocked LLM verifying tool is registered
- Existing filter agent tests should continue to pass
