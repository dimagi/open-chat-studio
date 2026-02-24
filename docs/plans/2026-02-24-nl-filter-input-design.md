# Natural Language Filter Input — Design

**Date:** 2026-02-24
**Branch:** sk/magic-filters-III

## Overview

Add a natural language input to the top of the table filter panel. Users type a query (e.g. "sessions from last week excluding WhatsApp"), click "✨ Generate", and the existing `FilterAgent` backend translates it into structured filter rows that populate the standard filter UI.

## Scope

- Frontend only: one new HTML section + three state properties + one method in `templates/experiments/filters.html`
- No backend changes (existing `/a/<team_slug>/help/filter/` endpoint is sufficient)
- No Waffle flag — available to all users
- No explanation banner or feedback buttons (deferred)

## UI Layout

```
┌──────────────────────────────────────────────────────────────┐
│  [sessions from last week, channel = WhatsApp     ] [✨ Generate] │
│  ⚠ Couldn't understand that query. Try rephrasing.           │  ← error only
├──────────────────────────────────────────────────────────────┤
│  Where  [column ▼]  [operator ▼]  [value ▼]  ×              │
│  AND    ...                                                   │
│  + Add Filter    [Create Filter]  [My Saved Filters]         │
└──────────────────────────────────────────────────────────────┘
```

The NL input sits at the top of the filter panel `<div>` (before the existing `<div class="space-y-2">`). The text input retains its value in all states to allow iterative refinement.

## State

Three new properties on `filterComponent`:

| Property | Type | Purpose |
|---|---|---|
| `nlQuery` | `string` | Two-way bound to text input |
| `nlLoading` | `boolean` | Disables button, shows spinner |
| `nlError` | `string` | Non-empty triggers inline error message |

## Method: `generateFiltersFromNL()`

1. Guard: return early if `nlQuery.trim()` is empty
2. `nlLoading = true`, `nlError = ''`
3. POST to `{% url 'help:run_agent' request.team.slug 'filter' %}` with body `{query: nlQuery, filter_slug: "{{ df_table_type }}"}`
4. **On success:** replace `filterData.filters` with hydrated filter objects; call `triggerFilterChange()`
5. **On error** (HTTP error or `response.error` key): set `nlError = "Couldn't understand that query. Try rephrasing it."`
6. Always: `nlLoading = false`

## Filter Hydration

Each `{column, operator, value}` from the agent response maps to a full Alpine filter row:

```js
{
  column: item.column,
  operator: item.operator,
  value: isListOperator ? '' : item.value,
  selectedValues: isListOperator ? JSON.parse(item.value) : [],
  availableOperators: filterData.columns[item.column]?.operators || [],
  showOptions: false,
  searchQuery: '',
  filteredOptions: [...(filterData.columns[item.column]?.options || [])],
}
```

List operators are: `any of`, `all of`, `excludes`. Unknown columns are skipped silently.

## Edge Cases

- **Blank input**: Generate button disabled (`:disabled="!nlQuery.trim() || nlLoading"`)
- **Unknown column in response**: skip silently (agent is schema-aware; this is a safety net)
- **Backend 400/500 or network failure**: show generic error string via `.catch()`
- **Multi-turn refinement**: input text persists; user edits query and clicks Generate again to overwrite filters

## Files Changed

- `templates/experiments/filters.html` — only file modified
