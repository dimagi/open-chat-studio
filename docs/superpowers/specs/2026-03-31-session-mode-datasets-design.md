# Session Mode for Evaluation Datasets

**Issue:** https://github.com/dimagi/open-chat-studio/issues/3043
**Date:** 2026-03-31

## Problem

Currently, importing sessions into evaluation datasets creates one `EvaluationMessage` per human-AI message pair. A session with 10 exchanges produces 10 dataset items, and the evaluator runs 10 times — once per pair.

For holistic session evaluation ("Was this conversation successful overall?"), this is wasteful and conceptually wrong. We need a mode where one dataset item represents an entire session, and the evaluator runs once per session.

## Approach

Extend the existing `EvaluationMessage` model and evaluation pipeline rather than introducing new models. Add mode awareness to datasets and evaluation-mode awareness to evaluators, with validation on the eval config form to enforce compatibility.

## Data Model Changes

### `EvaluationDataset` — new `evaluation_mode` field

```python
evaluation_mode = CharField(
    choices=[("message", "Message"), ("session", "Session")],
    default="message",
)
```

- Immutable after creation (set at dataset creation time). Enforced by excluding the field from `EvaluationDatasetEditForm`.
- Determines how dataset items are created and how the dataset can be used in evaluations.

### `Evaluator` — new `evaluation_mode` field

> **Note:** The existing `type` field on `Evaluator` stores the evaluator class name (e.g., `"LlmEvaluator"`, `"PythonEvaluator"`). The new field is named `evaluation_mode` to avoid collision.

```python
evaluation_mode = CharField(
    choices=[("message", "Message"), ("session", "Session")],
    default="message",
)
```

- Determines which prompt variables are relevant for the evaluator.
- Message evaluators: `{input.content}`, `{output.content}`, `{context.[param]}`, `{full_history}`, `{generated_response}`
- Session evaluators: `{full_history}`, `{context.[param]}` (no `{input.content}`, `{output.content}`, or `{generated_response}` — session-mode messages have empty `input`/`output`)

### `EvaluationConfig` — validation only, no new fields

- Evaluator choices are **pre-filtered** based on the selected dataset's `evaluation_mode` (only evaluators with matching mode are shown).
- `EvaluationConfigForm.clean()` additionally rejects configurations where the dataset's `evaluation_mode` does not match all attached evaluators' `evaluation_mode` as a backend safety net.

### `EvaluationMessage` — no new fields

- `as_result_dict()` updated to include `participant_data` and `session_state` in the returned dict (currently omits them).

## Session-Mode Dataset Creation

Only the "clone from sessions" creation method is supported for session-mode datasets. Manual and CSV creation are out of scope.

### Implementation

Session-mode creation uses a **new, separate function** `make_session_evaluation_message()` (and corresponding classmethod, e.g. `create_from_sessions_as_session_mode`). This is intentionally not a parameterization of the existing `make_evaluation_messages_from_sessions` — the operations are fundamentally different (pairing messages vs. collecting an entire transcript). Creation is dispatched as an **async Celery task**, following the same pattern as message-mode clone.

### For each selected session:

1. Query all `ChatMessage` objects in the session, ordered chronologically.
2. Build the full transcript as the `history` list (all human + AI turns).
3. Create **one** `EvaluationMessage` with:
   - `input` = `{}` (empty dict — session-mode does not use input/output)
   - `output` = `{}` (empty dict)
   - `history` = full conversation transcript (all turns)
   - `participant_data` = snapshot from the last AI message's trace (empty dict if no AI messages exist)
   - `session_state` = snapshot from the last AI message's trace (empty dict if no AI messages exist)
   - `metadata` = `{"session_id": <external_id>, "experiment_id": <public_id>, "created_mode": "clone"}`
   - `input_chat_message` = `None` (existing nullable FK, not applicable for session-mode)
   - `expected_output_chat_message` = `None` (existing nullable FK, not applicable for session-mode)

### Empty `input`/`output` considerations

Session-mode messages have empty `input` and `output` dicts. The following code must handle this gracefully:

- `EvaluationMessage.__str__()`: Guard against empty dicts — display "Session evaluation" or the first line of history.
- `as_human_langchain_message()` / `as_ai_langchain_message()`: These do `self.input["content"]` which would `KeyError` on empty dicts. These are only called in bot generation (which is skipped for session-mode), so a code comment documenting this constraint is sufficient — no guard clauses needed.
- Evaluator prompt variable rendering: Already handled — `SafeAccessWrapper` returns empty string for missing keys, and `PythonEvaluator` already catches `ValidationError` on input/output.

### Duplicate detection

Keyed on `metadata.session_id` — skip sessions already imported into the dataset.

## Evaluator Changes

### Session evaluators

- Available prompt variables: `{full_history}`, `{context.[param]}`
- `{input.content}`, `{output.content}`, and `{generated_response}` are not available (session-mode messages have empty `input`/`output`, and bot generation does not apply).
- If a prompt template references an unavailable variable, substitute an empty string at runtime (graceful degradation).
- The evaluator UI shows the appropriate available variables based on the selected `evaluation_mode`.

### Message evaluators

No changes. Existing variables remain: `{input.content}`, `{output.content}`, `{context.[param]}`, `{full_history}`, `{generated_response}`.

## Evaluation Execution

No changes to the core execution pipeline. The existing flow works as-is:

1. `run_evaluation_task` iterates over `EvaluationMessage` items in the dataset.
2. For session-mode datasets, there is one `EvaluationMessage` per session, so the evaluator fires once per session automatically.
3. `evaluate_single_message_task` runs the evaluator against each message.
4. The evaluator prompt renders with available variables.

### Changes:

- **Bot generation is prevented at the form level** — the eval config form hides/disables the generation experiment section when a session-mode dataset is selected (via Alpine.js/HTMX), so `generation_experiment` will be `None` for session-mode runs. The existing `if generation_experiment is not None` guard in `evaluate_single_message_task` is sufficient; no additional runtime check needed.
- `EvaluationResult` stores results the same way — `output` contains `message`, `result`, and an empty `generated_response`.

## UI Changes

### Dataset creation form

- Add `evaluation_mode` as a Django form field (message/session) at the top of the form, before the existing creation method selector (clone/manual/csv).
- When session mode is selected, only the "clone from sessions" creation method is available (manual and CSV are hidden via JS).
- The rest of the clone flow is unchanged: session selection table, filters, etc.

### Evaluator creation form

- Add an `evaluation_mode` selector (message/session).
- Show the appropriate available prompt variables based on evaluation mode via JS-driven help text swap.
- For session mode, hide the `{generated_response}`, `{input.content}`, and `{output.content}` variables from the documentation/hints.

### Eval config form

- Pre-filter evaluator choices based on the selected dataset's `evaluation_mode`.
- `clean()` validates dataset `evaluation_mode` matches evaluator `evaluation_mode` as a backend safety net.
- When a session-mode dataset is selected, dynamically hide/disable the generation experiment section via Alpine.js/HTMX.

### Dataset detail view

No changes required initially. The existing table showing `input`, `output`, `history` columns works for session-mode items (session-mode items will show empty `input`/`output` columns).

## Test Requirements

### Factory updates

- Add `evaluation_mode` field to `EvaluationDatasetFactory` (default: `"message"`).
- Add `evaluation_mode` field to `EvaluatorFactory` (default: `"message"`).

### Form validation tests (`EvaluationConfigForm.clean()`)

- Test the new `evaluation_mode` mismatch rejection (dataset vs evaluator).
- Test the existing `clean()` validation logic (version selection) — currently untested.

### Session-mode creation tests

1. **Happy path**: Session with N turns → one `EvaluationMessage` with full history, empty `input`/`output`.
2. **Single-turn session**: Session with one human-AI pair → still one message.
3. **Orphaned last message**: Session ending with human message (no AI response) → still creates one message with empty `input`/`output` and empty `participant_data`/`session_state`.
4. **Human-only session**: Session with only human messages (no AI responses) → creates one message with empty `participant_data`/`session_state`.
5. **Empty session**: Session with no messages → no `EvaluationMessage` created.
6. **Duplicate detection**: Re-importing the same session → skipped via `metadata.session_id`.
7. **`participant_data` / `session_state` snapshot**: Verify these are captured from the last AI message's trace.
8. **Metadata structure**: Verify `session_id` and `experiment_id` are set correctly.

### Eval config form validation tests

- Verify pre-filtering of evaluators based on dataset `evaluation_mode`.
- Verify `clean()` rejects mismatched evaluator/dataset `evaluation_mode`.

## Design Decisions

Resolved during design review:

| Decision | Resolution |
|----------|------------|
| History format for session-mode | Same as existing: `{message_type, content, summary}` dicts |
| Session creation function | New `make_session_evaluation_message()`, not parameterizing existing function |
| `evaluation_mode` immutability | Exclude field from `EvaluationDatasetEditForm` entirely |
| GIN index on `EvaluationMessage.metadata` | Not needed — expected dataset sizes are small (< 100 items) |
| Eval config evaluator filtering | Pre-filter evaluators by dataset's `evaluation_mode` + `clean()` safety net |
| Bot generation skip mechanism | Form-level prevention only (hide generation experiment); existing `if generation_experiment is not None` guard is sufficient |
| `__str__` for session-mode messages | Show "Session evaluation" or first history line for empty input/output |
| Langchain methods + empty input/output | Comment documenting constraint; no guard clauses (only called in bot generation path) |
| `evaluation_mode` on creation form | Django form field |
| Evaluator prompt variable hints | JS-driven help text swap based on selected mode |
| `participant_data`/`session_state` source | Last AI message's trace; empty dict if no AI messages |
| Sessions with only human messages | Still create message with empty `participant_data`/`session_state` |
| `metadata.created_mode` value | `"clone"` — `evaluation_mode` on dataset is sufficient distinction |
| Session-mode clone creation | Async Celery task, same pattern as message-mode |
| Eval config generation experiment visibility | Dynamic hide/show via Alpine.js/HTMX |

## Out of Scope

- Online/checkpoint evaluations (evaluating sessions as they progress).
- CSV upload and manual creation for session-mode datasets.
- Direct `source_session` FK on `EvaluationMessage` (rely on metadata for now).
- `{participant_data.[param]}` and `{session_state.[param]}` as session evaluator prompt variables.
