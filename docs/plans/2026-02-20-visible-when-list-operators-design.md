# Design: VisibleWhen List Operators (`is_empty` / `is_not_empty`)

**Date:** 2026-02-20
**Status:** Approved

## Problem

The `VisibleWhen` class supports scalar comparisons (`==`, `!=`, `in`, `not_in`) but has no way to express "show this field only when a list field has at least one item." This is needed for fields like `max_results` in `LLMResponseWithPrompt`, which is only relevant when at least one collection index is selected.

## Solution

Add two new operators — `is_not_empty` and `is_empty` — to the existing `VisibleWhen` operator set. These operators ignore the `value` field and instead check whether the referenced field is an empty or non-empty list (or falsy/truthy value).

## Changes

### Backend (`apps/pipelines/nodes/base.py`)

Extend the `Literal` type for `VisibleWhen.operator`:

```python
operator: Literal["==", "!=", "in", "not_in", "is_empty", "is_not_empty"] = "=="
```

`value` is irrelevant for these operators; `None` by convention.

### Node definition (`apps/pipelines/nodes/nodes.py`)

Apply `visible_when` to `max_results` in `LLMResponseWithPrompt`:

```python
max_results: OptionalInt = Field(
    default=20,
    ge=1,
    le=100,
    description="The maximum number of results to retrieve from the index",
    json_schema_extra=UiSchema(
        widget=Widgets.range,
        visible_when=VisibleWhen(field="collection_index_ids", operator="is_not_empty"),
    ),
)
```

### Frontend TypeScript (`assets/javascript/apps/pipeline/types/nodeParams.ts`)

Extend the operator union:

```typescript
operator?: "==" | "!=" | "in" | "not_in" | "is_empty" | "is_not_empty";
```

### Frontend React (`assets/javascript/apps/pipeline/nodes/GetInputWidget.tsx`)

Add cases to `evaluateCondition`:

```typescript
case "is_empty":
  return !fieldValue || (Array.isArray(fieldValue) && fieldValue.length === 0);
case "is_not_empty":
  return !!fieldValue && (!Array.isArray(fieldValue) || fieldValue.length > 0);
```

### Alpine.js (`templates/evaluations/evaluator_form.html`)

Add the same two cases to the `isFieldVisible` switch block.

### Tests (`apps/pipelines/tests/test_ui_schema.py`)

- Test `is_not_empty` with non-empty list → visible
- Test `is_not_empty` with empty list → hidden
- Test `is_empty` with empty list → visible
- Test `is_empty` with non-empty list → hidden

## Out of Scope

- Expression-based visibility (e.g., `"collection_index_ids.length > 0"`)
- Transform-based operators (e.g., `transform="length"`)
- Any other `LLMResponseWithPrompt` field visibility changes beyond `max_results`
