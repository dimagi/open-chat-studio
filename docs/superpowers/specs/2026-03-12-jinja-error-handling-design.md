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

A new team-scoped API endpoint validates Jinja template syntax. It follows the same URL pattern and auth as existing pipeline endpoints (`@login_and_team_required`, `@csrf_exempt`):

```
POST /api/pipelines/<team_slug>/validate-jinja/
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

The endpoint runs two checks in sequence:

1. **Jinja syntax validation** via `SandboxedEnvironment().parse(template)` — parses the template AST without rendering, catching syntax errors but not undefined variable errors (which are only knowable at runtime). Uses the same `SandboxedEnvironment` as runtime for parity. Results returned with `"error"` severity.

2. **HTML linting** via `djlint.lint.lint_file()` with profile `"jinja"` — catches HTML issues like unclosed tags, malformed attributes, etc. Results returned with `"warning"` severity. Uses a curated rule set relevant to template fragments (not full HTML documents):
   - **H025**: Orphan/unclosed tags
   - **T027**: Unclosed string in template syntax
   - **T034**: Likely typo in template tags (`{% ... }` instead of `{% ... %}`)
   - **H020**: Empty tag pairs
   - **H021**: Inline styles (informational)
   - All other rules are ignored (e.g. H005 html lang, H007 DOCTYPE, H016 title, J004/J018 url_for — irrelevant for template fragments)

Since djlint operates on files, the endpoint writes the template to a `NamedTemporaryFile` for linting, then cleans up.

Response includes both error types with a `severity` field:
```json
{"errors": [
  {"line": 1, "column": 8, "message": "unexpected end of template", "severity": "error"},
  {"line": 3, "column": 0, "message": "H025 Tag seems to be an orphan.", "severity": "warning"}
]}
```

Empty template strings are valid and return `{"errors": []}`.

#### Frontend CodeMirror Linter

Add a CodeMirror `linter()` extension to the `JinjaEditor` component. On each edit (debounced), it calls the backend validation endpoint and maps returned errors to CodeMirror `Diagnostic` objects with line/column positions. This renders inline squiggles in the editor at the error location.

The linter function:
- Debounces calls to avoid excessive requests during typing
- Maps backend error responses to `{from, to, severity, message}` diagnostics
- Shows "error" severity (red squiggles) for Jinja syntax errors, "warning" severity (yellow squiggles) for HTML lint issues
- Clears stale diagnostics while a request is in-flight

Requires adding `@codemirror/lint` as an npm dependency.

#### Pydantic Backstop Validator

Add a shared `@field_validator` on Jinja template fields in both `RenderTemplate` and `SendEmail` nodes. This calls `SandboxedEnvironment().parse()` on save — **not** `render()`, since template variables are only available at runtime. This catches syntax errors even when the frontend linter didn't fire (e.g. API-based pipeline updates). Errors surface as field-level validation errors in the existing error display mechanism.

### 2. RenderTemplate Widget Upgrade

Change `RenderTemplate.template_string` from `Widgets.expandable_text` to `Widgets.jinja_template`.

Rename `OptionsSource.jinja_email_node` to `OptionsSource.jinja_node` since `RenderTemplate` and `SendEmail` share the same Jinja context (via `_build_jinja_context`) and the variable list is identical: `input`, `node_inputs`, `temp_state`, `session_state`, `participant_data`, `participant_details`, `participant_schedules`, `input_message_id`, `input_message_url`. Update references in both `SendEmail` field definitions and `views.py`.

Similarly rename `get_jinja_email_vars()` to `get_jinja_vars()` for clarity.

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

A utility function `format_jinja_error(exc: Exception, field_name: str, context: dict | None = None) -> str` that:
1. Categorizes the exception type (`UndefinedError`, `TemplateSyntaxError`, `SecurityError`, other)
2. Extracts line number from `TemplateSyntaxError` when available
3. Appends available top-level variable names for `UndefinedError` (when `context` is provided)
4. Returns the formatted string

The `context` parameter is optional because exceptions during `env.from_string()` (parsing phase) occur before the context dict is used. In that case, available variables are omitted from the message.

Both `RenderTemplate._process()` and `SendEmail._process()` call this in their except blocks before raising `PipelineNodeRunError`. Field names match the Pydantic field names (e.g. `"template_string"`, `"subject"`, `"recipient_list"`, `"body"`).

No changes to the `Trace` model or `format_exception_for_trace()` — the improved message is stored as-is in `Trace.error`.

## Files Modified

- `apps/pipelines/nodes/nodes.py` — RenderTemplate widget upgrade, `format_jinja_error` helper, updated except blocks, Pydantic backstop validator
- `apps/pipelines/nodes/base.py` — rename `OptionsSource.jinja_email_node` to `OptionsSource.jinja_node`
- `apps/pipelines/views.py` — add validate-jinja endpoint, update options source key
- `apps/pipelines/urls.py` — route for new endpoint
- `apps/utils/prompt.py` — rename `get_jinja_email_vars` to `get_jinja_vars`
- `assets/javascript/apps/pipeline/components/CodeMirrorEditor.tsx` — add linter extension to `JinjaEditor`
- `package.json` — add `@codemirror/lint` dependency

## Test Changes

- `apps/pipelines/tests/test_template_node.py` — test structured error messages for RenderTemplate
- `apps/pipelines/tests/test_nodes.py` — test structured error messages for SendEmail
- New test for the validate-jinja endpoint (Jinja syntax errors, HTML lint warnings, valid templates, empty input)
- Update `apps/pipelines/tests/node_schemas/SendEmail.json` and add/update RenderTemplate schema test

## Out of Scope

- Changes to the Trace model schema
- Adding Jinja linting to other node types (LLM prompt fields use a different templating approach)
- Client-side Jinja parsing (all validation goes through the Python backend for parity)
