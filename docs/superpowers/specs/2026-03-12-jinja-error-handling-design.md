# Jinja Error Handling for Pipeline Nodes

## Problem

Jinja template errors in the Email and Template pipeline nodes provide poor feedback to users. Syntax errors are not caught during editing, and runtime errors in traces lack actionable context (no field name, no available variables, no error categorization).

## Goals

1. Catch Jinja syntax errors at edit time with inline feedback in the CodeMirror editor
2. Provide structured, actionable runtime error messages in traces
3. Upgrade the RenderTemplate node to use the new JinjaEditor widget

## Design

### 1. Edit-time Jinja Validation

#### Backend Endpoint

A new API endpoint validates Jinja template syntax:

```
POST /api/pipelines/validate-jinja/
Content-Type: application/json

{"template": "{{ foo }"}
```

Response:
```json
{"errors": [{"line": 1, "column": 8, "message": "unexpected end of template, expected 'end of print statement'."}]}
```

Or when valid:
```json
{"errors": []}
```

Implementation uses `SandboxedEnvironment().parse(template)` — the same environment used at runtime, ensuring parity. The endpoint requires an authenticated session (same auth as existing pipeline endpoints).

#### Frontend CodeMirror Linter

Add a CodeMirror `linter()` extension to the `JinjaEditor` component. On each edit (debounced ~500ms), it calls the backend validation endpoint and maps returned errors to CodeMirror `Diagnostic` objects with line/column positions. This renders inline squiggles in the editor at the error location.

The linter function:
- Debounces calls to avoid excessive requests during typing
- Maps backend error responses to `{from, to, severity, message}` diagnostics
- Shows "error" severity for syntax errors

#### Pydantic Backstop Validator

Add a shared `@field_validator` on Jinja template fields in both `RenderTemplate` and `SendEmail` nodes. This calls `SandboxedEnvironment().parse()` on save, catching syntax errors even when the frontend linter didn't fire (e.g. API-based pipeline updates). Errors surface as field-level validation errors in the existing error display mechanism.

### 2. RenderTemplate Widget Upgrade

Change `RenderTemplate.template_string` from `Widgets.expandable_text` to `Widgets.jinja_template`.

Add a new `OptionsSource.jinja_template_node` enum value. Since `RenderTemplate` and `SendEmail` share the same Jinja context (via `_build_jinja_context`), the variable list is identical: `input`, `node_inputs`, `temp_state`, `session_state`, `participant_data`, `participant_details`, `participant_schedules`, `input_message_id`, `input_message_url`.

The `get_jinja_email_vars()` method can be reused (or renamed to be more generic) since the variable set is the same.

### 3. Structured Runtime Error Messages

Replace the generic `f"Error rendering template: {e}"` pattern with a helper function that produces structured, actionable messages.

#### Error Format

For `UndefinedError`:
```
Jinja2 UndefinedError in field "subject": 'participant_data' is undefined
Available variables: input, node_inputs, temp_state, session_state, input_message_id, input_message_url
```

For `TemplateSyntaxError`:
```
Jinja2 TemplateSyntaxError in field "body": unexpected end of template (line 3)
```

For `SecurityError`:
```
Jinja2 SecurityError in field "template_string": access to attribute 'mro' of 'type' object is unsafe
```

For other exceptions:
```
Jinja2 error in field "body": <exception type>: <message>
```

#### Implementation

A utility function `format_jinja_error(exc: Exception, field_name: str, context: dict) -> str` that:
1. Categorizes the exception type (`UndefinedError`, `TemplateSyntaxError`, `SecurityError`, other)
2. Extracts line number from `TemplateSyntaxError` when available
3. Appends available top-level variable names for `UndefinedError`
4. Returns the formatted string

Both `RenderTemplate._process()` and `SendEmail._process()` call this in their except blocks before raising `PipelineNodeRunError`.

No changes to the `Trace` model or `format_exception_for_trace()` — the improved message is stored as-is in `Trace.error`.

## Files Modified

- `apps/pipelines/nodes/nodes.py` — RenderTemplate widget upgrade, `format_jinja_error` helper, updated except blocks
- `apps/pipelines/nodes/base.py` — add `OptionsSource.jinja_template_node`
- `apps/pipelines/views.py` — add validate-jinja endpoint, add jinja_template_node to options source data
- `apps/pipelines/urls.py` — route for new endpoint
- `apps/utils/prompt.py` — reuse or rename `get_jinja_email_vars` for shared use
- `assets/javascript/apps/pipeline/components/CodeMirrorEditor.tsx` — add linter extension to `JinjaEditor`

## Test Changes

- `apps/pipelines/tests/test_template_node.py` — test structured error messages for RenderTemplate
- `apps/pipelines/tests/test_nodes.py` — test structured error messages for SendEmail
- New test for the validate-jinja endpoint (syntax errors, valid templates, empty input)
- Update `apps/pipelines/tests/node_schemas/SendEmail.json` and add/update RenderTemplate schema test

## Out of Scope

- Changes to the Trace model schema
- Adding Jinja linting to other node types (LLM prompt fields use a different templating approach)
- Client-side Jinja parsing (all validation goes through the Python backend for parity)
