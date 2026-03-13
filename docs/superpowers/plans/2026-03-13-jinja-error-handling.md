# Jinja Error Handling Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide better Jinja error feedback during editing (inline linting) and at runtime (structured trace errors) for the Email and Template pipeline nodes.

**Architecture:** A backend validation endpoint runs Jinja syntax parsing + djlint HTML linting, exposed to a CodeMirror linter extension in the JinjaEditor. Runtime errors in node `_process()` methods are reformatted with error type, field name, and available variables. A Pydantic backstop validator catches syntax errors on save.

**Tech Stack:** Python (Django, Jinja2, djlint, Pydantic), TypeScript (CodeMirror 6 with `@codemirror/lint`, `@codemirror/lang-jinja`)

**Spec:** `docs/superpowers/specs/2026-03-12-jinja-error-handling-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `apps/pipelines/nodes/nodes.py` | Modify | `format_jinja_error` helper, updated except blocks, Pydantic backstop validator, RenderTemplate widget upgrade |
| `apps/pipelines/nodes/base.py` | Modify | Rename `OptionsSource.jinja_email_node` to `jinja_node` |
| `apps/pipelines/views.py` | Modify | Add `validate_jinja` view with `checks` parameter, update options source key |
| `apps/pipelines/urls.py` | Modify | Add route for validate-jinja endpoint |
| `apps/utils/prompt.py` | Modify | Rename `get_jinja_email_vars` to `get_jinja_vars` |
| `assets/javascript/apps/pipeline/api/api.ts` | Modify | Add `validateJinja` method with `checks` parameter |
| `assets/javascript/apps/pipeline/components/CodeMirrorEditor.tsx` | Modify | Add linter extension to `JinjaEditor` via `onValidate` prop |
| `assets/javascript/apps/pipeline/nodes/widgets.tsx` | Modify | Pass `onValidate` prop with appropriate `checks` to `JinjaEditor` |
| `apps/pipelines/tests/test_jinja_validation.py` | Create | Tests for `format_jinja_error`, backstop validator, validate-jinja endpoint |
| `apps/pipelines/tests/test_template_node.py` | Modify | Add structured error message tests |
| `apps/pipelines/tests/test_nodes.py` | Modify | Add structured error message tests for SendEmail |
| `apps/pipelines/tests/node_schemas/SendEmail.json` | Modify | Update `jinja_email_node` → `jinja_node` |
| `apps/pipelines/tests/node_schemas/RenderTemplate.json` | Modify | Update widget from `expandable_text` to `jinja_template` |

---

## Chunk 1: Backend — Structured Runtime Errors + Backstop Validator

### Task 1: `format_jinja_error` helper and tests

**Files:**
- Create: `apps/pipelines/tests/test_jinja_validation.py`
- Modify: `apps/pipelines/nodes/nodes.py:1-22` (imports), `apps/pipelines/nodes/nodes.py:97-152` (near RenderTemplate)

- [ ] **Step 1: Write failing tests for `format_jinja_error`**

Create `apps/pipelines/tests/test_jinja_validation.py`. Tests construct exception objects directly rather than triggering them via Jinja — this is a pure unit test of the formatter:

```python
import pytest
from jinja2 import TemplateSyntaxError, UndefinedError
from jinja2.sandbox import SecurityError

from apps.pipelines.nodes.nodes import format_jinja_error


class TestFormatJinjaError:
    def test_undefined_error_with_context(self):
        exc = UndefinedError("'foo' is undefined")
        result = format_jinja_error(exc, "subject", context={"input": "", "temp_state": {}})
        assert 'UndefinedError in field "subject"' in result
        assert "Available variables: input, temp_state" in result

    def test_undefined_error_without_context(self):
        exc = UndefinedError("'foo' is undefined")
        result = format_jinja_error(exc, "body")
        assert 'UndefinedError in field "body"' in result
        assert "Available variables" not in result

    def test_syntax_error(self):
        exc = TemplateSyntaxError("unexpected '}'", lineno=3)
        result = format_jinja_error(exc, "template_string")
        assert 'TemplateSyntaxError in field "template_string"' in result
        assert "(line 3)" in result

    def test_syntax_error_no_lineno(self):
        exc = TemplateSyntaxError("unexpected end of template", lineno=None)
        result = format_jinja_error(exc, "body")
        assert 'TemplateSyntaxError in field "body"' in result
        assert "(line" not in result

    def test_security_error(self):
        exc = SecurityError("access to attribute 'mro' of 'type' object is unsafe")
        result = format_jinja_error(exc, "body")
        assert 'SecurityError in field "body"' in result

    def test_generic_exception(self):
        exc = ValueError("something broke")
        result = format_jinja_error(exc, "body")
        assert 'Jinja2 error in field "body"' in result
        assert "ValueError" in result

    def test_context_keys_preserve_insertion_order(self):
        """Available variables should appear in insertion order, not sorted."""
        from collections import OrderedDict

        ctx = OrderedDict([("zebra", 1), ("alpha", 2), ("middle", 3)])
        exc = UndefinedError("'foo' is undefined")
        result = format_jinja_error(exc, "body", context=ctx)
        assert "Available variables: zebra, alpha, middle" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/pipelines/tests/test_jinja_validation.py -v`
Expected: FAIL — `cannot import name 'format_jinja_error'`

- [ ] **Step 3: Implement `format_jinja_error`**

Add to `apps/pipelines/nodes/nodes.py`, after the existing imports (line 22) and before `_build_jinja_context` (line 97). Add the necessary imports at the top of the file:

```python
# Add to imports at top of file:
from jinja2 import TemplateSyntaxError, UndefinedError
from jinja2.sandbox import SecurityError as JinjaSandboxSecurityError
```

```python
def format_jinja_error(exc: Exception, field_name: str, context: dict | None = None) -> str:
    """Format a Jinja2 exception into a structured, actionable error message.

    Args:
        exc: The Jinja2 exception that was raised.
        field_name: The Pydantic field name where the error occurred.
        context: The Jinja template context dict, if available. Used to list
            available variables for UndefinedError.
    """
    exc_type = type(exc).__name__

    if isinstance(exc, UndefinedError):
        msg = f'Jinja2 UndefinedError in field "{field_name}": {exc}'
        if context:
            var_names = ", ".join(context.keys())
            msg += f"\nAvailable variables: {var_names}"
        return msg

    if isinstance(exc, TemplateSyntaxError):
        line_info = f" (line {exc.lineno})" if exc.lineno else ""
        return f'Jinja2 TemplateSyntaxError in field "{field_name}": {exc.message}{line_info}'

    if isinstance(exc, JinjaSandboxSecurityError):
        return f'Jinja2 SecurityError in field "{field_name}": {exc}'

    return f'Jinja2 error in field "{field_name}": {exc_type}: {exc}'
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/pipelines/tests/test_jinja_validation.py -v`
Expected: PASS

- [ ] **Step 5: Lint**

Run: `uv run ruff check apps/pipelines/nodes/nodes.py apps/pipelines/tests/test_jinja_validation.py --fix && uv run ruff format apps/pipelines/nodes/nodes.py apps/pipelines/tests/test_jinja_validation.py`

- [ ] **Step 6: Commit**

```bash
git add apps/pipelines/nodes/nodes.py apps/pipelines/tests/test_jinja_validation.py
git commit -m "feat: add format_jinja_error helper for structured Jinja error messages"
```

---

### Task 2: Wire `format_jinja_error` into RenderTemplate and SendEmail

**Files:**
- Modify: `apps/pipelines/nodes/nodes.py:143-152` (RenderTemplate._process), `apps/pipelines/nodes/nodes.py:465-499` (SendEmail._process)
- Modify: `apps/pipelines/tests/test_template_node.py`
- Modify: `apps/pipelines/tests/test_nodes.py`

- [ ] **Step 1: Write failing test for RenderTemplate structured error**

Add to `apps/pipelines/tests/test_template_node.py`:

```python
@pytest.mark.django_db()
def test_render_template_undefined_variable_error(pipeline, experiment_session):
    state = PipelineState(
        experiment_session=experiment_session,
        messages=["hello"],
        outputs={},
    )
    node = RenderTemplate(name="test", node_id="123", django_node=None, template_string="{{ nonexistent_var }}")
    config = {"configurable": {"repo": ORMRepository(session=experiment_session)}}

    with pytest.raises(PipelineNodeRunError, match=r'UndefinedError in field "template_string"'):
        node.process(incoming_nodes=[], outgoing_nodes=[], state=state, config=config)
```

Add imports at the top of the test file:
```python
from apps.pipelines.exceptions import PipelineNodeRunError
```

- [ ] **Step 2: Write failing tests for SendEmail structured errors**

Add to `apps/pipelines/tests/test_nodes.py`. Use `InMemoryPipelineRepository` to avoid DB access:

```python
from apps.pipelines.repository import InMemoryPipelineRepository


class TestSendEmailRuntimeErrors:
    def _make_state(self):
        return PipelineState(messages=["hello"], outputs={})

    def _make_config(self):
        repo = InMemoryPipelineRepository()
        return {"configurable": {"repo": repo}}

    def test_subject_undefined_error(self):
        node = SendEmail(
            name="email",
            node_id="test",
            django_node=None,
            recipient_list="test@example.com",
            subject="{{ nonexistent }}",
        )
        with pytest.raises(PipelineNodeRunError, match=r'UndefinedError in field "subject"'):
            node.process(incoming_nodes=[], outgoing_nodes=[], state=self._make_state(), config=self._make_config())

    def test_body_undefined_error(self):
        node = SendEmail(
            name="email",
            node_id="test",
            django_node=None,
            recipient_list="test@example.com",
            subject="Hi",
            body="{{ missing_var }}",
        )
        with pytest.raises(PipelineNodeRunError, match=r'UndefinedError in field "body"'):
            node.process(incoming_nodes=[], outgoing_nodes=[], state=self._make_state(), config=self._make_config())

    def test_recipient_list_undefined_error(self):
        node = SendEmail(
            name="email",
            node_id="test",
            django_node=None,
            recipient_list="{{ missing_var }}",
            subject="Hi",
        )
        with pytest.raises(PipelineNodeRunError, match=r'UndefinedError in field "recipient_list"'):
            node.process(incoming_nodes=[], outgoing_nodes=[], state=self._make_state(), config=self._make_config())
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest apps/pipelines/tests/test_template_node.py::test_render_template_undefined_variable_error apps/pipelines/tests/test_nodes.py::TestSendEmailRuntimeErrors -v`
Expected: FAIL — error messages don't match the structured format yet

- [ ] **Step 4: Update RenderTemplate._process to use format_jinja_error**

In `apps/pipelines/nodes/nodes.py`, replace the `_process` method of `RenderTemplate` (lines 143-152):

```python
    def _process(self, state: PipelineState, context: "NodeContext") -> PipelineState:
        env = SandboxedEnvironment()
        jinja_context = _build_jinja_context(context, self.repo)
        try:
            template = env.from_string(self.template_string)
        except Exception as e:
            raise PipelineNodeRunError(format_jinja_error(e, "template_string")) from e
        try:
            output = template.render(jinja_context)
        except Exception as e:
            raise PipelineNodeRunError(format_jinja_error(e, "template_string", context=jinja_context)) from e

        return PipelineState.from_node_output(node_name=self.name, node_id=self.node_id, output=output)
```

- [ ] **Step 5: Update SendEmail._process to use format_jinja_error**

In `apps/pipelines/nodes/nodes.py`, replace the `_process` method of `SendEmail` (lines 465-499):

```python
    def _process(self, state: PipelineState, context: "NodeContext") -> PipelineState:
        env = SandboxedEnvironment()
        env.filters["split"] = lambda value, sep=",": str(value).split(sep)
        jinja_context = _build_jinja_context(context, self.repo)

        try:
            subject = env.from_string(self.subject).render(jinja_context)
        except Exception as e:
            raise PipelineNodeRunError(format_jinja_error(e, "subject", context=jinja_context)) from e

        # Strip newlines to prevent email header injection
        subject = subject.replace("\r", "").replace("\n", " ")

        try:
            rendered_recipients = env.from_string(self.recipient_list).render(jinja_context)
        except Exception as e:
            raise PipelineNodeRunError(format_jinja_error(e, "recipient_list", context=jinja_context)) from e

        recipients = [r.strip() for r in rendered_recipients.split(",") if r.strip()]
        for email in recipients:
            try:
                validate_email(email)
            except ValidationError:
                raise PipelineNodeRunError(f"Invalid email address after rendering: {email!r}") from None

        if self.body:
            try:
                message = env.from_string(self.body).render(jinja_context)
            except Exception as e:
                raise PipelineNodeRunError(format_jinja_error(e, "body", context=jinja_context)) from e
        else:
            message = context.input

        send_email_from_pipeline.delay(recipient_list=recipients, subject=subject, message=message)
        return PipelineState.from_node_output(node_name=self.name, node_id=self.node_id, output=context.input)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest apps/pipelines/tests/test_template_node.py apps/pipelines/tests/test_nodes.py -v`
Expected: ALL PASS

- [ ] **Step 7: Lint**

Run: `uv run ruff check apps/pipelines/nodes/nodes.py apps/pipelines/tests/test_template_node.py apps/pipelines/tests/test_nodes.py --fix && uv run ruff format apps/pipelines/nodes/nodes.py apps/pipelines/tests/test_template_node.py apps/pipelines/tests/test_nodes.py`

- [ ] **Step 8: Commit**

```bash
git add apps/pipelines/nodes/nodes.py apps/pipelines/tests/test_template_node.py apps/pipelines/tests/test_nodes.py
git commit -m "feat: use structured Jinja error messages in RenderTemplate and SendEmail"
```

---

### Task 3: Pydantic backstop validator

**Files:**
- Modify: `apps/pipelines/nodes/nodes.py` (add validator near RenderTemplate and SendEmail)
- Modify: `apps/pipelines/tests/test_jinja_validation.py`

- [ ] **Step 1: Write failing tests for the backstop validator**

Add to `apps/pipelines/tests/test_jinja_validation.py`:

```python
from pydantic_core import ValidationError

from apps.pipelines.nodes.nodes import RenderTemplate, SendEmail


class TestJinjaSyntaxBackstopValidator:
    def test_render_template_valid_syntax(self):
        node = RenderTemplate(name="test", node_id="1", django_node=None, template_string="{{ foo }}")
        assert node.template_string == "{{ foo }}"

    def test_render_template_invalid_syntax(self):
        with pytest.raises(ValidationError, match="Invalid Jinja2 syntax"):
            RenderTemplate(name="test", node_id="1", django_node=None, template_string="{{ foo }")

    def test_render_template_empty_string(self):
        """Empty strings are valid — parse("") succeeds."""
        node = RenderTemplate(name="test", node_id="1", django_node=None, template_string="")
        assert node.template_string == ""

    def test_send_email_body_invalid_syntax(self):
        with pytest.raises(ValidationError, match="Invalid Jinja2 syntax"):
            SendEmail(
                name="email",
                node_id="1",
                django_node=None,
                recipient_list="test@example.com",
                subject="Hi",
                body="{% if foo %}oops",
            )

    def test_send_email_subject_invalid_syntax(self):
        with pytest.raises(ValidationError, match="Invalid Jinja2 syntax"):
            SendEmail(
                name="email",
                node_id="1",
                django_node=None,
                recipient_list="test@example.com",
                subject="{{ broken }",
            )

    def test_send_email_recipient_list_invalid_syntax(self):
        with pytest.raises(ValidationError, match="Invalid Jinja2 syntax"):
            SendEmail(
                name="email",
                node_id="1",
                django_node=None,
                recipient_list="{{ broken }",
                subject="Hi",
            )

    def test_send_email_valid_jinja_fields(self):
        node = SendEmail(
            name="email",
            node_id="1",
            django_node=None,
            recipient_list="{{ participant_data.email }}",
            subject="Hello {{ input }}",
            body="Body: {{ input }}",
        )
        assert "participant_data.email" in node.recipient_list
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/pipelines/tests/test_jinja_validation.py::TestJinjaSyntaxBackstopValidator -v`
Expected: FAIL — invalid syntax doesn't raise ValidationError yet

- [ ] **Step 3: Implement the backstop validator**

Add a shared validator function in `apps/pipelines/nodes/nodes.py`, near `format_jinja_error`:

```python
def _validate_jinja_syntax(value: str) -> str:
    """Pydantic validator that checks Jinja2 template syntax at save time.

    Uses env.parse() (AST only, no rendering) so it only catches syntax errors,
    not undefined variable errors (which require runtime context).
    """
    if not value:
        return value
    try:
        SandboxedEnvironment().parse(value)
    except TemplateSyntaxError as e:
        line_info = f" (line {e.lineno})" if e.lineno else ""
        raise PydanticCustomError(
            "invalid_jinja_syntax",
            f"Invalid Jinja2 syntax: {e.message}{line_info}",
        ) from None
    return value
```

Note: The error message is pre-formatted (f-string, not PydanticCustomError template interpolation) because Jinja error messages frequently contain `{` and `}` which would cause double-interpolation issues.

Then add the validator to `RenderTemplate`:

```python
    @field_validator("template_string", mode="before")
    def validate_template_syntax(cls, value):
        return _validate_jinja_syntax(value)
```

For `SendEmail`, chain the Jinja check into the existing `recipient_list_has_valid_emails` validator (to avoid implicit ordering dependencies), and add a separate validator for `subject` and `body`:

```python
    @field_validator("subject", "body", mode="before")
    def validate_jinja_syntax(cls, value):
        return _validate_jinja_syntax(value)

    @field_validator("recipient_list", mode="before")
    def recipient_list_has_valid_emails(cls, value):
        value = value or ""
        # Check Jinja syntax first — if it's invalid, don't try email validation
        _validate_jinja_syntax(value)
        if "{{" in value or "{%" in value:
            return value  # Jinja2 template — validate at runtime after rendering
        for email in [email.strip() for email in value.split(",")]:
            try:
                validate_email(email)
            except ValidationError:
                raise PydanticCustomError("invalid_recipient_list", "Invalid list of emails addresses") from None
        return value
```

This replaces the existing `recipient_list_has_valid_emails` method — the only change is the `_validate_jinja_syntax(value)` call at the top.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/pipelines/tests/test_jinja_validation.py -v`
Expected: ALL PASS

- [ ] **Step 5: Run existing tests to verify no regressions**

Run: `uv run pytest apps/pipelines/tests/test_nodes.py::TestSendEmailInputValidation -v`
Expected: ALL PASS — the existing email validation tests should still work because templates containing `{{` are valid Jinja syntax.

- [ ] **Step 6: Lint**

Run: `uv run ruff check apps/pipelines/nodes/nodes.py apps/pipelines/tests/test_jinja_validation.py --fix && uv run ruff format apps/pipelines/nodes/nodes.py apps/pipelines/tests/test_jinja_validation.py`

- [ ] **Step 7: Commit**

```bash
git add apps/pipelines/nodes/nodes.py apps/pipelines/tests/test_jinja_validation.py
git commit -m "feat: add Pydantic backstop validator for Jinja syntax errors on save"
```

---

### Task 4: Rename OptionsSource + RenderTemplate widget upgrade

**Files:**
- Modify: `apps/pipelines/nodes/base.py:443` (rename enum)
- Modify: `apps/pipelines/nodes/nodes.py:138-141` (RenderTemplate field), `apps/pipelines/nodes/nodes.py:435-450` (SendEmail fields)
- Modify: `apps/pipelines/views.py:249` (options source mapping)
- Modify: `apps/utils/prompt.py:34` (rename function)
- Modify: `apps/pipelines/tests/node_schemas/SendEmail.json`
- Modify: `apps/pipelines/tests/node_schemas/RenderTemplate.json`

- [ ] **Step 1: Rename `OptionsSource.jinja_email_node` to `OptionsSource.jinja_node`**

In `apps/pipelines/nodes/base.py` line 443, change:
```python
    jinja_email_node = "jinja_email_node"
```
to:
```python
    jinja_node = "jinja_node"
```

- [ ] **Step 2: Rename `get_jinja_email_vars` to `get_jinja_vars`**

In `apps/utils/prompt.py` line 34, change:
```python
    def get_jinja_email_vars() -> list[dict]:
```
to:
```python
    def get_jinja_vars() -> list[dict]:
```

- [ ] **Step 3: Update `views.py` to use the new names**

In `apps/pipelines/views.py` line 249, change:
```python
        OptionsSource.jinja_email_node: PromptVars.get_jinja_email_vars(),
```
to:
```python
        OptionsSource.jinja_node: PromptVars.get_jinja_vars(),
```

- [ ] **Step 4: Update SendEmail fields to use `OptionsSource.jinja_node`**

In `apps/pipelines/nodes/nodes.py`, update all three SendEmail fields. Change every `OptionsSource.jinja_email_node` to `OptionsSource.jinja_node` (3 occurrences in the `recipient_list`, `subject`, and `body` field definitions).

- [ ] **Step 5: Upgrade RenderTemplate.template_string widget**

In `apps/pipelines/nodes/nodes.py`, change the `template_string` field (around line 138) from:
```python
    template_string: str = Field(
        description="Use {{your_variable_name}} to refer to designate input",
        json_schema_extra=UiSchema(widget=Widgets.expandable_text),
    )
```
to:
```python
    template_string: str = Field(
        description="Use {{your_variable_name}} to refer to designate input",
        json_schema_extra=UiSchema(widget=Widgets.jinja_template, options_source=OptionsSource.jinja_node),
    )
```

- [ ] **Step 6: Update node schema test files**

Update `apps/pipelines/tests/node_schemas/SendEmail.json` — replace all 3 occurrences of `"jinja_email_node"` with `"jinja_node"`.

Update `apps/pipelines/tests/node_schemas/RenderTemplate.json` — change:
```json
    "template_string": {
      "description": "Use {{your_variable_name}} to refer to designate input",
      "title": "Template String",
      "type": "string",
      "ui:widget": "expandable_text"
    }
```
to:
```json
    "template_string": {
      "description": "Use {{your_variable_name}} to refer to designate input",
      "title": "Template String",
      "type": "string",
      "ui:optionsSource": "jinja_node",
      "ui:widget": "jinja_template"
    }
```

- [ ] **Step 7: Run schema tests to verify**

Run: `uv run pytest apps/pipelines/tests/ -v -k "schema"`
Expected: PASS

- [ ] **Step 8: Run all pipeline tests for regression check**

Run: `uv run pytest apps/pipelines/tests/ -v`
Expected: ALL PASS

- [ ] **Step 9: Lint**

Run: `uv run ruff check apps/pipelines/nodes/base.py apps/pipelines/nodes/nodes.py apps/pipelines/views.py apps/utils/prompt.py --fix && uv run ruff format apps/pipelines/nodes/base.py apps/pipelines/nodes/nodes.py apps/pipelines/views.py apps/utils/prompt.py`

- [ ] **Step 10: Commit**

```bash
git add apps/pipelines/nodes/base.py apps/pipelines/nodes/nodes.py apps/pipelines/views.py apps/utils/prompt.py apps/pipelines/tests/node_schemas/SendEmail.json apps/pipelines/tests/node_schemas/RenderTemplate.json
git commit -m "refactor: rename OptionsSource.jinja_email_node to jinja_node and upgrade RenderTemplate widget"
```

---

## Chunk 2: Backend — Validate-Jinja Endpoint

### Task 5: Validate-Jinja endpoint and tests

**Files:**
- Modify: `apps/pipelines/views.py` (add new view)
- Modify: `apps/pipelines/urls.py` (add route)
- Modify: `apps/pipelines/tests/test_jinja_validation.py` (add endpoint tests)

- [ ] **Step 1: Write failing tests for the validate-jinja endpoint**

Add to `apps/pipelines/tests/test_jinja_validation.py`:

```python
import json

from django.test import Client
from django.urls import reverse

from apps.utils.factories.team import TeamWithUsersFactory


@pytest.mark.django_db()
class TestValidateJinjaEndpoint:
    @pytest.fixture()
    def team_with_users(self):
        return TeamWithUsersFactory.create()

    @pytest.fixture()
    def authed_client(self, team_with_users):
        client = Client()
        user = team_with_users.members.first()
        client.force_login(user)
        return client

    def _url(self, team):
        return reverse("pipelines:validate_jinja", kwargs={"team_slug": team.slug})

    def _post(self, client, team, data):
        return client.post(
            self._url(team),
            data=json.dumps(data),
            content_type="application/json",
        )

    def test_valid_template(self, authed_client, team_with_users):
        response = self._post(authed_client, team_with_users, {"template": "Hello {{ name }}"})
        assert response.status_code == 200
        assert response.json()["errors"] == []

    def test_empty_template(self, authed_client, team_with_users):
        response = self._post(authed_client, team_with_users, {"template": ""})
        assert response.status_code == 200
        assert response.json()["errors"] == []

    def test_jinja_syntax_error(self, authed_client, team_with_users):
        response = self._post(authed_client, team_with_users, {"template": "{{ foo }"})
        assert response.status_code == 200
        data = response.json()
        assert len(data["errors"]) >= 1
        error = data["errors"][0]
        assert error["severity"] == "error"
        assert error["line"] is not None
        assert "message" in error

    def test_unclosed_html_tag(self, authed_client, team_with_users):
        response = self._post(authed_client, team_with_users, {"template": "<div><p>{{ foo }}</div>"})
        assert response.status_code == 200
        data = response.json()
        warnings = [e for e in data["errors"] if e["severity"] == "warning"]
        assert len(warnings) >= 1
        assert any("H025" in w["message"] for w in warnings)

    def test_valid_html_no_warnings(self, authed_client, team_with_users):
        response = self._post(authed_client, team_with_users, {"template": "<div><p>{{ foo }}</p></div>"})
        assert response.status_code == 200
        assert response.json()["errors"] == []

    def test_excluded_djlint_rules_not_reported(self, authed_client, team_with_users):
        """H006 (img height/width) and H013 (img alt) should be filtered out."""
        response = self._post(authed_client, team_with_users, {"template": '<img src="test.png">'})
        assert response.status_code == 200
        assert response.json()["errors"] == []

    def test_checks_jinja_only(self, authed_client, team_with_users):
        """When checks=["jinja"], HTML lint warnings should not be returned."""
        response = self._post(
            authed_client, team_with_users,
            {"template": "<div><p>{{ foo }}</div>", "checks": ["jinja"]},
        )
        assert response.status_code == 200
        # No HTML warnings — only Jinja checks were requested, and this is valid Jinja
        assert response.json()["errors"] == []

    def test_checks_html_only(self, authed_client, team_with_users):
        """When checks=["html"], Jinja syntax errors should not be returned."""
        response = self._post(
            authed_client, team_with_users,
            {"template": "{{ foo }", "checks": ["html"]},
        )
        assert response.status_code == 200
        # No Jinja error — only HTML checks were requested
        # (djlint may or may not flag this, but there should be no "error" severity)
        errors = [e for e in response.json()["errors"] if e["severity"] == "error"]
        assert errors == []

    def test_checks_defaults_to_both(self, authed_client, team_with_users):
        """When checks is omitted, both Jinja and HTML checks run."""
        response = self._post(
            authed_client, team_with_users,
            {"template": "<div><p>{{ foo }}</div>"},
        )
        assert response.status_code == 200
        warnings = [e for e in response.json()["errors"] if e["severity"] == "warning"]
        assert len(warnings) >= 1

    def test_missing_template_field(self, authed_client, team_with_users):
        response = self._post(authed_client, team_with_users, {})
        assert response.status_code == 400

    def test_unauthenticated_request(self, team_with_users):
        client = Client()
        response = client.post(
            self._url(team_with_users),
            data=json.dumps({"template": "{{ foo }}"}),
            content_type="application/json",
        )
        # login_and_team_required redirects unauthenticated users
        assert response.status_code == 302
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/pipelines/tests/test_jinja_validation.py::TestValidateJinjaEndpoint -v`
Expected: FAIL — URL not found

- [ ] **Step 3: Add the URL route**

In `apps/pipelines/urls.py`, add the route:

```python
urlpatterns = [
    path("data/<int:pk>/", views.pipeline_data, name="pipeline_data"),
    path("validate-jinja/", views.validate_jinja, name="validate_jinja"),
    path("<int:pipeline_pk>/message/", views.simple_pipeline_message, name="pipeline_message"),
    # ... rest unchanged
]
```

- [ ] **Step 4: Implement the validate_jinja view**

Add to `apps/pipelines/views.py`:

```python
import json
import os
import tempfile
from pathlib import Path

from djlint.lint import lint_file
from djlint.settings import Config as DjlintConfig
from jinja2 import TemplateSyntaxError
from jinja2.sandbox import SandboxedEnvironment


# Curated djlint rules relevant to template fragments.
# All other rules (H005 html lang, H007 DOCTYPE, H016 title, J004/J018 url_for, etc.)
# are irrelevant for template fragments and would produce noise.
DJLINT_ALLOWED_RULES = {"H020", "H021", "H025", "T027", "T034"}

# Use /dev/shm (RAM-backed tmpfs) if available to avoid disk I/O for temp files
_DJLINT_TMPDIR = "/dev/shm" if os.path.isdir("/dev/shm") else None


@login_and_team_required
@permission_required("pipelines.change_pipeline")
@csrf_exempt
@require_POST
def validate_jinja(request, team_slug: str):
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    template = body.get("template")
    if template is None:
        return JsonResponse({"error": "Missing 'template' field"}, status=400)

    checks = set(body.get("checks", ["jinja", "html"]))
    errors = []

    # 1. Jinja syntax validation
    if "jinja" in checks and template:
        try:
            SandboxedEnvironment().parse(template)
        except TemplateSyntaxError as e:
            errors.append({
                "line": e.lineno or 1,
                "column": 0,
                "message": e.message,
                "severity": "error",
            })

    # 2. HTML linting via djlint (only if requested and no Jinja syntax errors)
    if "html" in checks and not errors and template:
        errors.extend(_djlint_check(template))

    return JsonResponse({"errors": errors})


def _djlint_check(template: str) -> list[dict]:
    """Run djlint on a template string and return lint issues as dicts.

    Uses a curated allowlist of rules (DJLINT_ALLOWED_RULES) to filter out
    rules that are irrelevant for template fragments.
    """
    # TODO: DjlintConfig re-parses pyproject.toml on every call. Profile and
    # consider caching if this becomes a bottleneck.
    with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False, dir=_DJLINT_TMPDIR) as f:
        f.write(template)
        tmp_path = Path(f.name)
    try:
        config = DjlintConfig(str(tmp_path), profile="jinja")
        results = lint_file(config, tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    errors = []
    for issues in results.values():
        for issue in issues:
            code = issue.get("code", "")
            if code not in DJLINT_ALLOWED_RULES:
                continue
            line_str = issue.get("line", "1:0")
            parts = line_str.split(":")
            line = int(parts[0]) if parts[0].isdigit() else 1
            column = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
            errors.append({
                "line": line,
                "column": column,
                "message": f"{code} {issue.get('message', '')}".strip(),
                "severity": "warning",
            })
    return errors
```

Also ensure `require_POST` is imported (check if it already is):
```python
from django.views.decorators.http import require_POST
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest apps/pipelines/tests/test_jinja_validation.py::TestValidateJinjaEndpoint -v`
Expected: ALL PASS

- [ ] **Step 6: Lint**

Run: `uv run ruff check apps/pipelines/views.py apps/pipelines/urls.py apps/pipelines/tests/test_jinja_validation.py --fix && uv run ruff format apps/pipelines/views.py apps/pipelines/urls.py apps/pipelines/tests/test_jinja_validation.py`

- [ ] **Step 7: Commit**

```bash
git add apps/pipelines/views.py apps/pipelines/urls.py apps/pipelines/tests/test_jinja_validation.py
git commit -m "feat: add validate-jinja endpoint with Jinja syntax + djlint HTML linting"
```

---

## Chunk 3: Frontend — CodeMirror Linter Extension

### Task 6: Install @codemirror/lint dependency

**Files:**
- Modify: `package.json`

- [ ] **Step 1: Install the dependency**

Run: `npm install @codemirror/lint`

- [ ] **Step 2: Commit**

```bash
git add package.json package-lock.json
git commit -m "chore: add @codemirror/lint dependency for Jinja editor linting"
```

---

### Task 7: Add validateJinja method to API client

**Files:**
- Modify: `assets/javascript/apps/pipeline/api/api.ts`

- [ ] **Step 1: Add the method**

Add to the `ApiClient` class in `assets/javascript/apps/pipeline/api/api.ts`, after the `generateCode` method:

```typescript
  public async validateJinja(
    template: string,
    checks: string[] = ["jinja", "html"],
  ): Promise<{errors: Array<{line: number, column: number, message: string, severity: string}>}> {
    return this.makeRequest<{errors: Array<{line: number, column: number, message: string, severity: string}>}>(
      "post",
      `/pipelines/validate-jinja/`,
      { template, checks },
    );
  }
```

- [ ] **Step 2: Lint**

Run: `npm run lint assets/javascript/apps/pipeline/api/api.ts -- --fix`

- [ ] **Step 3: Commit**

```bash
git add assets/javascript/apps/pipeline/api/api.ts
git commit -m "feat: add validateJinja method to pipeline API client"
```

---

### Task 8: Add linter extension to JinjaEditor and wire into JinjaWidget

**Files:**
- Modify: `assets/javascript/apps/pipeline/components/CodeMirrorEditor.tsx`
- Modify: `assets/javascript/apps/pipeline/nodes/widgets.tsx`

- [ ] **Step 1: Add linter to JinjaEditor via onValidate prop**

In `assets/javascript/apps/pipeline/components/CodeMirrorEditor.tsx`, add the import:

```typescript
import {linter, Diagnostic} from "@codemirror/lint";
```

Then replace the `JinjaEditor` function (currently at line 259) with:

```typescript
type ValidateFn = (template: string) => Promise<{errors: Array<{line: number, column: number, message: string, severity: string}>}>;

function jinjaLinter(onValidate: ValidateFn) {
  return linter(async (view): Promise<Diagnostic[]> => {
    const doc = view.state.doc.toString();
    if (!doc.trim()) return [];

    try {
      const result = await onValidate(doc);
      return result.errors.map((err) => {
        const line = Math.min(err.line, view.state.doc.lines);
        const lineObj = view.state.doc.line(line);
        const from = lineObj.from + Math.min(err.column, lineObj.length);
        const to = Math.min(from + 1, lineObj.to);
        return {
          from,
          to,
          severity: err.severity === "error" ? "error" : "warning",
          message: err.message,
        } as Diagnostic;
      });
    } catch {
      return [];
    }
  }, {delay: 500});
}

export function JinjaEditor(
  {value, onChange, readOnly, autocompleteVars, onValidate}: {
    value: string;
    onChange: (value: string) => void;
    readOnly: boolean;
    autocompleteVars: string[];
    onValidate?: ValidateFn;
  }
) {
  const jinjaVariables = autocompleteVars.map((v) => ({ label: v, type: "variable" }));
  let extensions = [
    jinja({ variables: jinjaVariables }),
    EditorView.lineWrapping,
  ];
  if (onValidate) {
    extensions.push(jinjaLinter(onValidate));
  }
  if (readOnly) {
    extensions = [
      ...extensions,
      EditorView.editable.of(false),
      EditorState.readOnly.of(true),
    ];
  }
  return <CodeMirrorEditor value={value} onChange={onChange} extensions={extensions}/>;
}
```

- [ ] **Step 2: Wire JinjaWidget to pass onValidate**

In `assets/javascript/apps/pipeline/nodes/widgets.tsx`, update the `JinjaWidget` function to pass `onValidate` to `JinjaEditor`. Import `apiClient` at the top (already imported) and add the callback:

```typescript
export function JinjaWidget(props: WidgetParams) {
  const autocomplete_vars_list: string[] = getAutoCompleteList(getSelectOptions(props.schema));
  const rows: number = props.schema["ui:rows"] ?? 2;
  const modalId = useId();

  // Single-line fields (rows < 2) only get Jinja syntax checks, not HTML lint
  const checks = rows < 2 ? ["jinja"] : ["jinja", "html"];
  const onValidate = (template: string) => apiClient.validateJinja(template, checks);

  // ... rest of the existing code, but pass onValidate to JinjaEditor:
```

Update the `<JinjaEditor>` usage in the modal to include the prop:
```typescript
            <JinjaEditor
              value={Array.isArray(props.paramValue) ? props.paramValue.join('') : props.paramValue || ''}
              onChange={onChangeCallback}
              readOnly={props.readOnly}
              autocompleteVars={autocomplete_vars_list}
              onValidate={onValidate}
            />
```

- [ ] **Step 3: Lint and type-check**

Run: `npm run lint assets/javascript/apps/pipeline/components/CodeMirrorEditor.tsx assets/javascript/apps/pipeline/nodes/widgets.tsx -- --fix`
Run: `npm run type-check`

- [ ] **Step 4: Build frontend**

Run: `npm run dev`
Expected: Build succeeds with no errors

- [ ] **Step 5: Commit**

```bash
git add assets/javascript/apps/pipeline/components/CodeMirrorEditor.tsx assets/javascript/apps/pipeline/nodes/widgets.tsx
git commit -m "feat: add inline Jinja + HTML linting to JinjaEditor via CodeMirror"
```

---

## Chunk 4: Final Verification

### Task 9: Full test suite and lint check

- [ ] **Step 1: Run all pipeline tests**

Run: `uv run pytest apps/pipelines/tests/ -v`
Expected: ALL PASS

- [ ] **Step 2: Run full Python lint**

Run: `uv run ruff check apps/pipelines/ apps/utils/prompt.py --fix && uv run ruff format apps/pipelines/ apps/utils/prompt.py`

- [ ] **Step 3: Run frontend build**

Run: `npm run dev`
Expected: Build succeeds

- [ ] **Step 4: Run frontend lint**

Run: `npm run lint assets/javascript/apps/pipeline/ -- --fix`

- [ ] **Step 5: Type check**

Run: `uv run ty check apps/pipelines/`
