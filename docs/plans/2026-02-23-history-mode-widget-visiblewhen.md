# HistoryModeWidget visibleWhen Refactor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Slim `HistoryModeWidget` to only render the `history_mode` select + dynamic help text, and move `user_max_token_limit` / `max_history_length` to framework-driven fields with `visibleWhen` conditions.

**Architecture:** The backend `HistoryMixin` adds `visible_when` to each sub-field and removes their `widget=Widgets.none` override so the generic renderer picks them up. The frontend widget drops all state/markup for those two fields — `VisibleWhenWrapper` in `getInputWidget` handles show/hide and value-reset automatically.

**Tech Stack:** Python/Pydantic (backend schema), React/TypeScript (pipeline UI), pytest, Jest/ESLint

---

### Task 1: Write a failing test for the new mixin schema shape

**Files:**
- Modify: `apps/pipelines/tests/test_ui_schema.py`

**Step 1: Add a test asserting the new `ui:visibleWhen` keys**

Add at the bottom of `apps/pipelines/tests/test_ui_schema.py`:

```python
from apps.pipelines.nodes.mixins import HistoryMixin


def test_history_mixin_user_max_token_limit_visible_when():
    props = HistoryMixin.model_json_schema()["properties"]
    assert props["user_max_token_limit"]["ui:visibleWhen"] == {
        "field": "history_mode",
        "operator": "in",
        "value": ["summarize", "truncate_tokens"],
    }
    assert "ui:widget" not in props["user_max_token_limit"]


def test_history_mixin_max_history_length_visible_when():
    props = HistoryMixin.model_json_schema()["properties"]
    assert props["max_history_length"]["ui:visibleWhen"] == {
        "field": "history_mode",
        "operator": "==",
        "value": "max_history_length",
    }
    assert "ui:widget" not in props["max_history_length"]
```

**Step 2: Run the tests to verify they fail**

```bash
pytest apps/pipelines/tests/test_ui_schema.py::test_history_mixin_user_max_token_limit_visible_when apps/pipelines/tests/test_ui_schema.py::test_history_mixin_max_history_length_visible_when -v
```

Expected: FAIL — `KeyError: 'ui:visibleWhen'` (field currently has `"ui:widget": "none"` instead).

**Step 3: Commit the failing tests**

```bash
git add apps/pipelines/tests/test_ui_schema.py
git commit -m "test: add failing tests for HistoryMixin visibleWhen schema"
```

---

### Task 2: Update `HistoryMixin` to make the tests pass

**Files:**
- Modify: `apps/pipelines/nodes/mixins.py:159-174`

**Step 1: Read the current field definitions**

Open `apps/pipelines/nodes/mixins.py` and locate the `HistoryMixin` class (around line 148). The fields to change are:

```python
# BEFORE
user_max_token_limit: int | None = Field(
    None,
    json_schema_extra=UiSchema(
        widget=Widgets.none,
    ),
)
max_history_length: int = Field(
    10,
    json_schema_extra=UiSchema(
        widget=Widgets.none,
    ),
)
```

**Step 2: Replace both field definitions**

```python
# AFTER
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

Note: `VisibleWhen` is already imported at the top of `mixins.py` via `from apps.pipelines.nodes.base import ...`. Verify with:

```bash
grep "VisibleWhen" apps/pipelines/nodes/mixins.py
```

If not present, add it to the import.

**Step 3: Run the new tests to verify they pass**

```bash
pytest apps/pipelines/tests/test_ui_schema.py::test_history_mixin_user_max_token_limit_visible_when apps/pipelines/tests/test_ui_schema.py::test_history_mixin_max_history_length_visible_when -v
```

Expected: PASS

**Step 4: Run the full ui_schema test file**

```bash
pytest apps/pipelines/tests/test_ui_schema.py -v
```

Expected: all PASS

**Step 5: Lint**

```bash
ruff check apps/pipelines/nodes/mixins.py --fix
ruff format apps/pipelines/nodes/mixins.py
```

**Step 6: Commit**

```bash
git add apps/pipelines/nodes/mixins.py
git commit -m "feat: add visibleWhen to user_max_token_limit and max_history_length in HistoryMixin"
```

---

### Task 3: Regenerate schema fixtures and fix snapshot tests

**Files:**
- Modify: `apps/pipelines/tests/node_schemas/LLMResponseWithPrompt.json`
- Modify: `apps/pipelines/tests/node_schemas/RouterNode.json`

**Step 1: Verify snapshot tests currently fail**

```bash
pytest apps/pipelines/tests/test_schemas.py -v
```

Expected: FAIL for `LLMResponseWithPrompt` and `RouterNode` (schema has changed).

**Step 2: Regenerate all schema fixtures**

```bash
python manage.py update_pipeline_schema
```

This rewrites every `.json` file under `apps/pipelines/tests/node_schemas/` to match the current schema.

**Step 3: Verify the fixture changes are correct**

Check `LLMResponseWithPrompt.json` and `RouterNode.json` — the `user_max_token_limit` and `max_history_length` entries should now look like:

```json
"user_max_token_limit": {
  "default": null,
  "title": "Token Limit",
  "description": "Maximum number of tokens before messages are summarized or truncated.",
  "ui:visibleWhen": {
    "field": "history_mode",
    "operator": "in",
    "value": ["summarize", "truncate_tokens"]
  },
  "type": "integer"
},
"max_history_length": {
  "default": 10,
  "title": "Max History Length",
  "description": "Chat history will only keep the most recent messages up to this limit.",
  "ui:visibleWhen": {
    "field": "history_mode",
    "operator": "==",
    "value": "max_history_length"
  },
  "type": "integer"
}
```

There must be no `"ui:widget": "none"` on either field.

**Step 4: Run snapshot tests**

```bash
pytest apps/pipelines/tests/test_schemas.py -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add apps/pipelines/tests/node_schemas/
git commit -m "chore: regenerate pipeline schema fixtures after HistoryMixin changes"
```

---

### Task 4: Slim down `HistoryModeWidget` in the frontend

**Files:**
- Modify: `assets/javascript/apps/pipeline/nodes/widgets.tsx:1121-1197`

**Step 1: Locate the widget**

The `HistoryModeWidget` function starts at line 1121. It currently:
- Reads `userMaxTokenLimit`, `maxHistoryLength`, `defaultMaxTokens` from store/cache
- Manages a `historyMode` local state
- Renders the select + dynamic description
- Renders a conditional `Token Limit` input block (lines 1164–1178)
- Renders a conditional `Max History Length` input block (lines 1180–1194)

**Step 2: Replace `HistoryModeWidget` with the slim version**

Replace the entire function body (keep the `export function HistoryModeWidget(props: WidgetParams)` signature) with:

```tsx
export function HistoryModeWidget(props: WidgetParams) {
  const options = getSelectOptions(props.schema);
  const initialHistoryMode = concatenate(props.nodeParams["history_mode"]);
  const [historyMode, setHistoryMode] = useState(initialHistoryMode || "summarize");
  const historyModeHelpTexts: Record<string, string> = {
    summarize: "If the token count exceeds the limit, older messages will be summarized while keeping the last few messages intact.",
    truncate_tokens: "If the token count exceeds the limit, older messages will be removed until the token count is below the limit.",
    max_history_length: "The chat history will always be truncated to the last N messages.",
  };

  return (
    <div className="flex join">
      <InputField label="History Mode" help_text="">
        <select
          // Add `appearance-none` to work around placement issue: https://github.com/saadeghi/daisyui/discussions/4202
          // Should be resolved in future versions of browsers.
          className="select appearance-none join-item w-full"
          name="history_mode"
          onChange={(e) => {
            setHistoryMode(e.target.value);
            props.updateParamValue(e);
          }}
          value={historyMode}
          disabled={props.readOnly}
        >
          {options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <small className="text-muted mt-2">{historyModeHelpTexts[historyMode]}</small>
      </InputField>
    </div>
  );
}
```

Removed: `userMaxTokenLimit`, `maxHistoryLength`, `llmProviderId`, `models`, `model`, `defaultMaxTokens` variables and the two conditional input blocks.

**Step 3: Check for unused imports**

The removed code used `getCachedData` and `LlmProviderModel`. Check if they are still used elsewhere in the file before removing any imports:

```bash
grep -n "getCachedData\|LlmProviderModel" assets/javascript/apps/pipeline/nodes/widgets.tsx
```

Only remove an import if its usage count drops to zero.

**Step 4: Lint the file**

```bash
npm run lint assets/javascript/apps/pipeline/nodes/widgets.tsx
```

Fix any warnings reported.

**Step 5: TypeScript type-check**

```bash
npm run type-check assets/javascript/apps/pipeline/nodes/widgets.tsx
```

Expected: no errors.

**Step 6: Commit**

```bash
git add assets/javascript/apps/pipeline/nodes/widgets.tsx
git commit -m "refactor: slim HistoryModeWidget to select+description only; sub-fields use visibleWhen"
```

---

### Task 5: Final verification

**Step 1: Run all pipeline-related backend tests**

```bash
pytest apps/pipelines/tests/ -v
```

Expected: all PASS

**Step 2: Run the full backend test suite (smoke check)**

```bash
pytest apps/pipelines/ apps/service_providers/ -v --tb=short -q
```

Expected: all PASS

**Step 3: Build the frontend assets**

```bash
npm run dev
```

Expected: builds without errors or warnings related to changed files.
