# Session Mode for Evaluation Datasets

**Issue:** https://github.com/dimagi/open-chat-studio/issues/3043
**Date:** 2026-03-31

## Problem

Currently, importing sessions into evaluation datasets creates one `EvaluationMessage` per human-AI message pair. A session with 10 exchanges produces 10 dataset items, and the evaluator runs 10 times — once per pair.

For holistic session evaluation ("Was this conversation successful overall?"), this is wasteful and conceptually wrong. We need a mode where one dataset item represents an entire session, and the evaluator runs once per session.

## Approach

Extend the existing `EvaluationMessage` model and evaluation pipeline rather than introducing new models. Add mode awareness to datasets and type awareness to evaluators, with validation on the eval config to enforce compatibility.

## Data Model Changes

### `EvaluationDataset` — new `mode` field

```python
mode = CharField(
    choices=[("message", "Message"), ("session", "Session")],
    default="message",
)
```

- Immutable after creation (set at dataset creation time).
- Determines how dataset items are created and how the dataset can be used in evaluations.

### `Evaluator` — new `type` field

```python
type = CharField(
    choices=[("message", "Message"), ("session", "Session")],
    default="message",
)
```

- Determines which prompt variables are relevant for the evaluator.
- Message evaluators: `{input.content}`, `{output.content}`, `{context.[param]}`, `{full_history}`, `{generated_response}`
- Session evaluators: `{input.content}`, `{output.content}`, `{context.[param]}`, `{full_history}` (no `{generated_response}`)

### `EvaluationConfig` — validation only, no new fields

- `clean()` rejects configurations where the dataset's `mode` does not match all attached evaluators' `type`.

## Session-Mode Dataset Creation

Only the "clone from sessions" creation method is supported for session-mode datasets. Manual and CSV creation are out of scope.

### For each selected session:

1. Query all `ChatMessage` objects in the session, ordered chronologically.
2. Build the full transcript as the `history` list (all human + AI turns).
3. Find the last human-AI pair:
   - If the session ends with a complete pair: `input` = last human message, `output` = last AI response.
   - If the session ends with an orphaned human message (no AI response): `input` = last human message, `output` = empty.
4. Create **one** `EvaluationMessage` with:
   - `input` = last human message content
   - `output` = last AI response content (or empty)
   - `history` = full conversation transcript (all turns)
   - `participant_data` = snapshot from the last message's trace
   - `session_state` = snapshot from the last message's trace
   - `metadata` = `{"session_id": <external_id>, "experiment_id": <public_id>, "created_mode": "clone"}`

### Duplicate detection

Keyed on `metadata.session_id` — skip sessions already imported into the dataset.

## Evaluator Changes

### Session evaluators

- Available prompt variables: `{input.content}`, `{output.content}`, `{context.[param]}`, `{full_history}`
- `{generated_response}` is not available (bot generation does not apply to session-mode evals).
- The evaluator UI shows the appropriate available variables based on the selected type.

### Message evaluators

No changes. Existing variables remain: `{input.content}`, `{output.content}`, `{context.[param]}`, `{full_history}`, `{generated_response}`.

## Evaluation Execution

No changes to the core execution pipeline. The existing flow works as-is:

1. `run_evaluation_task` iterates over `EvaluationMessage` items in the dataset.
2. For session-mode datasets, there is one `EvaluationMessage` per session, so the evaluator fires once per session automatically.
3. `evaluate_single_message_task` runs the evaluator against each message.
4. The evaluator prompt renders with available variables.

### Changes:

- **Skip bot generation** for session-mode eval runs. Check `dataset.mode` on the eval run and skip the `run_bot_generation()` call when mode is `session`.
- `EvaluationResult` stores results the same way — `output` contains `message`, `result`, and an empty `generated_response`.

## UI Changes

### Dataset creation form

- Add a mode selector (message/session) at the top of the form, before the existing creation method selector (clone/manual/csv).
- When session mode is selected, only the "clone from sessions" creation method is available (manual and CSV are hidden).
- The rest of the clone flow is unchanged: session selection table, filters, etc.

### Evaluator creation form

- Add a type selector (message/session).
- Show the appropriate available prompt variables based on type.
- For session type, hide the `{generated_response}` variable from the documentation/hints.

### Eval config form

- Validate dataset mode matches evaluator types. Show a validation error on mismatch.
- When a session-mode dataset is selected, hide/disable the generation experiment section.

### Dataset detail view

No changes required initially. The existing table showing `input`, `output`, `history` columns works for session-mode items.

## Out of Scope

- Online/checkpoint evaluations (evaluating sessions as they progress).
- CSV upload and manual creation for session-mode datasets.
- Direct `source_session` FK on `EvaluationMessage` (rely on metadata for now).
- `{participant_data.[param]}` and `{session_state.[param]}` as session evaluator prompt variables.
