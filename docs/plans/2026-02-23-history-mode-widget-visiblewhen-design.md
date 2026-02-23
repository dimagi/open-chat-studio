# Design: Refactor HistoryModeWidget to use visibleWhen

## Summary

Slim down `HistoryModeWidget` so it only renders the `history_mode` select and its dynamic per-mode help text. Move the two subordinate fields (`user_max_token_limit`, `max_history_length`) out of the widget and into the standard field-rendering pipeline, driven by `visibleWhen` conditions on the backend schema.

## Motivation

The current `HistoryModeWidget` owns the conditional rendering of two fields that are logically independent of the widget itself. The `visibleWhen` pattern already handles this concern framework-wide (see `VisibleWhenWrapper`), including automatic value-reset when a field is hidden. Using the standard pattern reduces custom frontend code and makes the schema self-describing.

## Approach: Option A — Slim custom widget + visibleWhen sub-fields

### Backend (`apps/pipelines/nodes/mixins.py`)

`history_mode` keeps `widget=Widgets.history_mode`. The two sub-fields change from `widget=Widgets.none` to standard integer widgets with `visible_when` conditions and proper `title`/`description`:

```python
user_max_token_limit: int | None = Field(
    None,
    title="Token Limit",
    description="Maximum number of tokens before messages are summarized or truncated.",
    json_schema_extra=UiSchema(
        visible_when=VisibleWhen(
            field="history_mode",
            operator="in",
            value=["summarize", "truncate_tokens"],
        ),
    ),
)
max_history_length: int = Field(
    10,
    title="Max History Length",
    description="Chat history will only keep the most recent messages up to this limit.",
    json_schema_extra=UiSchema(
        visible_when=VisibleWhen(
            field="history_mode",
            value="max_history_length",
        ),
    ),
)
```

### Frontend (`assets/javascript/apps/pipeline/nodes/widgets.tsx`)

`HistoryModeWidget` is reduced to:
- The `history_mode` select dropdown
- The dynamic per-mode description paragraph beneath it

Removed from the widget:
- The `Token Limit` input block
- The `Max History Length` input block
- All state/logic for `userMaxTokenLimit`, `maxHistoryLength`, `defaultMaxTokens`

These fields are now rendered by `getInputWidget` → `VisibleWhenWrapper` like any other field.

### Schema fixtures (`apps/pipelines/tests/node_schemas/`)

Update fixture JSON files to reflect the changed schema:
- `user_max_token_limit` and `max_history_length`: remove `"ui:widget": "none"`, add `"ui:visibleWhen"` conditions and proper titles/descriptions.

### What is preserved

- Per-mode dynamic help text on `history_mode` itself (kept in the slim widget).
- Backend fallback: if `user_max_token_limit` is `None`, the history middleware reads `max_token_limit` from the selected LLM model (`mixins.py:264-267`). Behavior is unchanged; the widget's previous auto-fill was cosmetic only.

### What is dropped

- Auto-population of the `user_max_token_limit` input from the LLM model's `max_token_limit`. The field now starts blank; backend fallback still applies.

## Testing

- Update snapshot tests that assert on the node JSON schema.
- Confirm `visibleWhen` show/hide and value-reset work in the pipeline UI for both sub-fields.
