# Dynamic Send Email Node — Design

**Issue:** [#2986](https://github.com/dimagi/open-chat-studio/issues/2986)
**Date:** 2026-03-09

## Problem

The `SendEmail` pipeline node has static `subject` and `recipient_list` fields. Users cannot personalise emails per participant or route emails based on session/participant state without adding a separate `RenderTemplate` node before the email node. The body always comes from `context.input`.

## Goals

1. Allow `subject` and `recipient_list` to be dynamically populated from participant data, temp state, or session state.
2. Incorporate a Jinja2 template body field directly into `SendEmail`, removing the need for a preceding `RenderTemplate` node.

## Non-goals

- Changing the email sending infrastructure (`send_email_from_pipeline` task).
- Supporting attachments or HTML-only templates.

## Design

### Fields

| Field | Type | Change | Syntax |
|---|---|---|---|
| `recipient_list` | `str` | Modified: supports dynamic interpolation | Python `{format}` |
| `subject` | `str` | Modified: supports dynamic interpolation | Python `{format}` |
| `body` | `str` | **New**, optional, default `""` | Jinja2 `{{ variable }}` |

#### `recipient_list` and `subject` — Python format strings

Consistent with how other pipeline nodes (e.g. `LLMResponseWithPrompt`, routing nodes) handle dynamic prompt fields. Uses `PromptTemplateContext` + `SafeAccessWrapper` for safe dot-access on nested data.

Available variables:
- `{participant_data.key}` — participant data
- `{temp_state.key}` — pipeline temporary state
- `{session_state.key}` — session state
- `{current_datetime}` — current datetime
- `{input}` — current pipeline input

Validation change: construction-time email validation is skipped when `recipient_list` contains `{` (template syntax detected). Rendered output is validated at runtime via `validate_email()`. Invalid addresses at runtime raise `PipelineNodeRunError`.

#### `body` — Jinja2 template (new, optional)

Identical in capability to the `RenderTemplate` node. When left blank (default), falls back to `context.input`, preserving backwards compatibility with existing pipelines.

Available variables (same as `RenderTemplate`):
- `input`, `node_inputs`
- `temp_state`, `session_state`
- `participant_data`, `participant_details`, `participant_schedules`
- `input_message_id`, `input_message_url`

UI widget: `expandable_text` (same as `RenderTemplate`).

### Runtime Processing

1. Build `PromptTemplateContext` with `extra={"input": context.input, "temp_state": ..., "session_state": ...}` and `participant_data`.
2. Render `subject` via `get_context()` + `str.format()`.
3. Render `recipient_list` via same approach, split on `,`, strip whitespace, validate each address — raise `PipelineNodeRunError` on failure.
4. If `body` is non-empty, render with Jinja2 `SandboxedEnvironment`; else use `context.input`.
5. Dispatch `send_email_from_pipeline.delay(recipient_list, subject, message)`.

### Backwards Compatibility

- Existing pipelines: `body` defaults to `""` → body comes from `context.input` as before.
- Existing static `subject` and `recipient_list` values (no `{`) pass through `str.format()` unchanged.
- No database migration required (Pydantic model stored as JSON; new optional field uses default).

## Files Changed

| File | Change |
|---|---|
| `apps/pipelines/nodes/nodes.py` | Update `SendEmail`: field validator, `_process`, add `body` field |
| `apps/pipelines/tests/test_nodes.py` | Update validation tests; add tests for dynamic subject, recipients, body |
| `apps/pipelines/tests/utils.py` | Update `email_node()` helper to support optional `body` parameter |
