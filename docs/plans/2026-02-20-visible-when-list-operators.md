# VisibleWhen List Operators Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add `is_empty` and `is_not_empty` operators to `VisibleWhen`, then use `is_not_empty` on `max_results` in `LLMResponseWithPrompt` so it only renders when at least one collection index is selected.

**Architecture:** Three-layer change: Python model (operator Literal + node field), TypeScript types + React evaluation logic, Alpine.js evaluation logic. Tests are added before implementation in each layer.

**Tech Stack:** Python/Pydantic, Django, TypeScript, React, Alpine.js

---

### Task 1: Extend VisibleWhen operator set (Python + tests)

**Files:**
- Modify: `apps/pipelines/nodes/base.py:515`
- Test: `apps/pipelines/tests/test_ui_schema.py`

**Step 1: Write failing tests**

Open `apps/pipelines/tests/test_ui_schema.py` and add these two tests after the existing `test_visible_when_single_condition_not_in` test (around line 75):

```python
def test_visible_when_is_not_empty():
    class TestModel(PipelineNode):
        model_config = ConfigDict(json_schema_extra=NodeSchema(label="Test"))
        items: list[int] = Field(
            default_factory=list,
            json_schema_extra=UiSchema(visible_when=VisibleWhen(field="items", operator="is_not_empty")),
        )

    schema = TestModel.model_json_schema()
    assert schema["properties"]["items"]["ui:visibleWhen"] == {
        "field": "items",
        "operator": "is_not_empty",
        "value": None,
    }


def test_visible_when_is_empty():
    class TestModel(PipelineNode):
        model_config = ConfigDict(json_schema_extra=NodeSchema(label="Test"))
        items: list[int] = Field(
            default_factory=list,
            json_schema_extra=UiSchema(visible_when=VisibleWhen(field="items", operator="is_empty")),
        )

    schema = TestModel.model_json_schema()
    assert schema["properties"]["items"]["ui:visibleWhen"] == {
        "field": "items",
        "operator": "is_empty",
        "value": None,
    }
```

**Step 2: Run tests to verify they fail**

```bash
pytest apps/pipelines/tests/test_ui_schema.py::test_visible_when_is_not_empty apps/pipelines/tests/test_ui_schema.py::test_visible_when_is_empty -v
```

Expected: FAIL — `pydantic_core.core_schema.PydanticCustomError` or validation error because `is_not_empty` is not a valid operator.

**Step 3: Extend the operator Literal**

In `apps/pipelines/nodes/base.py` at line 515, change:

```python
operator: Literal["==", "!=", "in", "not_in"] = "=="
```

to:

```python
operator: Literal["==", "!=", "in", "not_in", "is_empty", "is_not_empty"] = "=="
```

**Step 4: Run tests to verify they pass**

```bash
pytest apps/pipelines/tests/test_ui_schema.py -v
```

Expected: ALL PASS.

**Step 5: Commit**

```bash
git add apps/pipelines/nodes/base.py apps/pipelines/tests/test_ui_schema.py
git commit -m "feat: add is_empty/is_not_empty operators to VisibleWhen"
```

---

### Task 2: Apply `is_not_empty` to `max_results` field

**Files:**
- Modify: `apps/pipelines/nodes/nodes.py:220-226`

This field already exists — just add `visible_when` to its `UiSchema`.

**Step 1: Modify the field**

In `apps/pipelines/nodes/nodes.py`, replace lines 220–226:

```python
max_results: OptionalInt = Field(
    default=20,
    ge=1,
    le=100,
    description="The maximum number of results to retrieve from the index",
    json_schema_extra=UiSchema(widget=Widgets.range),
)
```

with:

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

`VisibleWhen` is already imported via `apps/pipelines/nodes/base.py` in the imports block at the top of the file.

**Step 2: Run the existing LLMResponseWithPrompt schema test (if any) or spot-check**

```bash
pytest apps/pipelines/tests/ -v -k "llm" 2>/dev/null || pytest apps/pipelines/tests/ -v
```

Expected: PASS — no regressions.

**Step 3: Commit**

```bash
git add apps/pipelines/nodes/nodes.py
git commit -m "feat: hide max_results when no collection indexes selected"
```

---

### Task 3: Extend TypeScript types

**Files:**
- Modify: `assets/javascript/apps/pipeline/types/nodeParams.ts:9`

**Step 1: Update the operator union**

In `assets/javascript/apps/pipeline/types/nodeParams.ts`, change line 9 from:

```typescript
operator?: "==" | "!=" | "in" | "not_in";
```

to:

```typescript
operator?: "==" | "!=" | "in" | "not_in" | "is_empty" | "is_not_empty";
```

**Step 2: Type-check**

```bash
npm run type-check assets/javascript/apps/pipeline/types/nodeParams.ts
```

Expected: no type errors.

**Step 3: Commit**

```bash
git add assets/javascript/apps/pipeline/types/nodeParams.ts
git commit -m "feat: add is_empty/is_not_empty to VisibleWhenCondition TypeScript type"
```

---

### Task 4: Extend React evaluateCondition

**Files:**
- Modify: `assets/javascript/apps/pipeline/nodes/GetInputWidget.tsx:14-20`

**Step 1: Add new cases to the switch**

In `assets/javascript/apps/pipeline/nodes/GetInputWidget.tsx`, the `evaluateCondition` function currently looks like (lines 11–21):

```typescript
function evaluateCondition(condition: VisibleWhenCondition, nodeParams: NodeParams): boolean {
  const fieldValue = nodeParams[condition.field];
  const operator = condition.operator ?? "==";
  switch (operator) {
    case "==": return fieldValue === condition.value;
    case "!=": return fieldValue !== condition.value;
    case "in": return Array.isArray(condition.value) && condition.value.includes(fieldValue);
    case "not_in": return Array.isArray(condition.value) && !condition.value.includes(fieldValue);
    default: return true;
  }
}
```

Add two cases before `default`:

```typescript
function evaluateCondition(condition: VisibleWhenCondition, nodeParams: NodeParams): boolean {
  const fieldValue = nodeParams[condition.field];
  const operator = condition.operator ?? "==";
  switch (operator) {
    case "==": return fieldValue === condition.value;
    case "!=": return fieldValue !== condition.value;
    case "in": return Array.isArray(condition.value) && condition.value.includes(fieldValue);
    case "not_in": return Array.isArray(condition.value) && !condition.value.includes(fieldValue);
    case "is_empty": return !fieldValue || (Array.isArray(fieldValue) && fieldValue.length === 0);
    case "is_not_empty": return !!fieldValue && (!Array.isArray(fieldValue) || fieldValue.length > 0);
    default: return true;
  }
}
```

**Step 2: Lint and type-check**

```bash
npm run lint assets/javascript/apps/pipeline/nodes/GetInputWidget.tsx
npm run type-check assets/javascript/apps/pipeline/nodes/GetInputWidget.tsx
```

Expected: no errors.

**Step 3: Commit**

```bash
git add assets/javascript/apps/pipeline/nodes/GetInputWidget.tsx
git commit -m "feat: evaluate is_empty/is_not_empty operators in pipeline node visibility"
```

---

### Task 5: Extend Alpine.js isFieldVisible

**Files:**
- Modify: `templates/evaluations/evaluator_form.html:560-566`

**Step 1: Add new cases to the switch**

In `templates/evaluations/evaluator_form.html`, find the `isFieldVisible` method (around line 552). The switch block currently looks like:

```javascript
switch (operator) {
  case '==': return fieldValue === condition.value;
  case '!=': return fieldValue !== condition.value;
  case 'in': return Array.isArray(condition.value) && condition.value.includes(fieldValue);
  case 'not_in': return Array.isArray(condition.value) && !condition.value.includes(fieldValue);
  default: return true;
}
```

Add two cases before `default`:

```javascript
switch (operator) {
  case '==': return fieldValue === condition.value;
  case '!=': return fieldValue !== condition.value;
  case 'in': return Array.isArray(condition.value) && condition.value.includes(fieldValue);
  case 'not_in': return Array.isArray(condition.value) && !condition.value.includes(fieldValue);
  case 'is_empty': return !fieldValue || (Array.isArray(fieldValue) && fieldValue.length === 0);
  case 'is_not_empty': return !!fieldValue && (!Array.isArray(fieldValue) || fieldValue.length > 0);
  default: return true;
}
```

**Step 2: Lint**

```bash
npm run lint templates/evaluations/evaluator_form.html
```

Expected: no errors (or skip if the linter doesn't cover HTML templates).

**Step 3: Commit**

```bash
git add templates/evaluations/evaluator_form.html
git commit -m "feat: evaluate is_empty/is_not_empty operators in evaluator form visibility"
```

---

### Task 6: Final verification

**Step 1: Run full Python test suite for affected modules**

```bash
pytest apps/pipelines/tests/ -v
```

Expected: ALL PASS.

**Step 2: Run Python linter**

```bash
ruff check apps/pipelines/nodes/base.py apps/pipelines/nodes/nodes.py apps/pipelines/tests/test_ui_schema.py --fix
ruff format apps/pipelines/nodes/base.py apps/pipelines/nodes/nodes.py apps/pipelines/tests/test_ui_schema.py
```

Expected: no unfixed errors.

**Step 3: Type-check Python**

```bash
ty check apps/pipelines/nodes/base.py apps/pipelines/nodes/nodes.py
```

Expected: no errors.
