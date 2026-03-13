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
| `apps/pipelines/views.py` | Modify | Add `validate_jinja` view, update options source key |
| `apps/pipelines/urls.py` | Modify | Add route for validate-jinja endpoint |
| `apps/utils/prompt.py` | Modify | Rename `get_jinja_email_vars` to `get_jinja_vars` |
| `assets/javascript/apps/pipeline/api/api.ts` | Modify | Add `validateJinja` method |
| `assets/javascript/apps/pipeline/components/CodeMirrorEditor.tsx` | Modify | Add linter extension to `JinjaEditor` |
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

Create `apps/pipelines/tests/test_jinja_validation.py`:

```python
import pytest
from jinja2 import TemplateSyntaxError, UndefinedError
from jinja2.sandbox import SandboxedEnvironment, SecurityError

from apps.pipelines.nodes.nodes import format_jinja_error


class TestFormatJinjaError:
    def test_undefined_error_with_context(self):
        try:
            SandboxedEnvironment().from_string("{{ foo }}").render({})
        except UndefinedError as e:
            result = format_jinja_error(e, "subject", context={"input": "", "temp_state": {}})
        assert "UndefinedError" in result
        assert 'field "subject"' in result
        assert "Available variables: input, temp_state" in result

    def test_undefined_error_without_context(self):
        try:
            SandboxedEnvironment().from_string("{{ foo }}").render({})
        except UndefinedError as e:
            result = format_jinja_error(e, "body")
        assert "UndefinedError" in result
        assert 'field "body"' in result
        assert "Available variables" not in result

    def test_syntax_error(self):
        try:
            SandboxedEnvironment().parse("{{ foo }")
        except TemplateSyntaxError as e:
            result = format_jinja_error(e, "template_string")
        assert "TemplateSyntaxError" in result
        assert 'field "template_string"' in result

    def test_security_error(self):
        env = SandboxedEnvironment()
        try:
            env.from_string("{{ ''.__class__.__mro__ }}").render()
        except SecurityError as e:
            result = format_jinja_error(e, "body")
        assert "SecurityError" in result
        assert 'field "body"' in result

    def test_generic_exception(self):
        exc = ValueError("something broke")
        result = format_jinja_error(exc, "body")
        assert "Jinja2 error" in result
        assert 'field "body"' in result
        assert "ValueError" in result
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
            var_names = ", ".join(sorted(context.keys()))
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

Add the `PipelineNodeRunError` import at the top of the test file:
```python
from apps.pipelines.exceptions import PipelineNodeRunError
```

- [ ] **Step 2: Write failing test for SendEmail structured error**

Add to `apps/pipelines/tests/test_nodes.py`, inside a new test class or after the existing `TestSendEmailInputValidation` class:

```python
class TestSendEmailRuntimeErrors:
    @pytest.mark.django_db()
    def test_subject_undefined_error(self):
        session = ExperimentSessionFactory.create()
        state = PipelineState(
            experiment_session=session,
            messages=["hello"],
            outputs={},
        )
        node = SendEmail(
            name="email",
            node_id="test",
            django_node=None,
            recipient_list="test@example.com",
            subject="{{ nonexistent }}",
        )
        config = {"configurable": {"repo": ORMRepository(session=session)}}

        with pytest.raises(PipelineNodeRunError, match=r'UndefinedError in field "subject"'):
            node.process(incoming_nodes=[], outgoing_nodes=[], state=state, config=config)

    @pytest.mark.django_db()
    def test_body_undefined_error(self):
        session = ExperimentSessionFactory.create()
        state = PipelineState(
            experiment_session=session,
            messages=["hello"],
            outputs={},
        )
        node = SendEmail(
            name="email",
            node_id="test",
            django_node=None,
            recipient_list="test@example.com",
            subject="Hi",
            body="{{ missing_var }}",
        )
        config = {"configurable": {"repo": ORMRepository(session=session)}}

        with pytest.raises(PipelineNodeRunError, match=r'UndefinedError in field "body"'):
            node.process(incoming_nodes=[], outgoing_nodes=[], state=state, config=config)

    @pytest.mark.django_db()
    def test_recipient_list_undefined_error(self):
        session = ExperimentSessionFactory.create()
        state = PipelineState(
            experiment_session=session,
            messages=["hello"],
            outputs={},
        )
        node = SendEmail(
            name="email",
            node_id="test",
            django_node=None,
            recipient_list="{{ missing_var }}",
            subject="Hi",
        )
        config = {"configurable": {"repo": ORMRepository(session=session)}}

        with pytest.raises(PipelineNodeRunError, match=r'UndefinedError in field "recipient_list"'):
            node.process(incoming_nodes=[], outgoing_nodes=[], state=state, config=config)
```

Add the `ORMRepository` import at the top of `test_nodes.py`:
```python
from apps.pipelines.repository import ORMRepository
from apps.utils.factories.experiment import ExperimentSessionFactory
```

Note: `ExperimentSessionFactory` is already imported.

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
        """Empty strings are valid — the field is required but an empty template parses fine."""
        # Note: this will fail Pydantic's required-field check, not the Jinja validator.
        # If the field has a value (even empty), parse("") succeeds.
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
    """Pydantic validator that checks Jinja2 template syntax at save time."""
    if not value:
        return value
    try:
        SandboxedEnvironment().parse(value)
    except TemplateSyntaxError as e:
        line_info = f" (line {e.lineno})" if e.lineno else ""
        raise PydanticCustomError(
            "invalid_jinja_syntax",
            "Invalid Jinja2 syntax: {message}{line_info}",
            {"message": e.message, "line_info": line_info},
        ) from None
    return value
```

Then add the validator to `RenderTemplate`:

```python
    @field_validator("template_string", mode="before")
    def validate_template_syntax(cls, value):
        return _validate_jinja_syntax(value)
```

And to `SendEmail` (add as a new validator, keeping the existing `recipient_list_has_valid_emails`):

```python
    @field_validator("subject", "body", "recipient_list", mode="before")
    def validate_jinja_syntax(cls, value):
        return _validate_jinja_syntax(value)
```

**Important:** The `validate_jinja_syntax` validator on SendEmail must run **before** the existing `recipient_list_has_valid_emails` validator. Since Pydantic runs validators in definition order per field, place `validate_jinja_syntax` before `recipient_list_has_valid_emails` in the class body. Alternatively, since they both use `mode="before"`, the Jinja check will only run on the raw input and the email validator runs after — this is fine because a Jinja syntax error in `recipient_list` will be caught before the email validator tries to parse addresses.

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

    def test_valid_template(self, authed_client, team_with_users):
        response = authed_client.post(
            self._url(team_with_users),
            data=json.dumps({"template": "Hello {{ name }}"}),
            content_type="application/json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["errors"] == []

    def test_empty_template(self, authed_client, team_with_users):
        response = authed_client.post(
            self._url(team_with_users),
            data=json.dumps({"template": ""}),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json()["errors"] == []

    def test_jinja_syntax_error(self, authed_client, team_with_users):
        response = authed_client.post(
            self._url(team_with_users),
            data=json.dumps({"template": "{{ foo }"}),
            content_type="application/json",
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["errors"]) >= 1
        error = data["errors"][0]
        assert error["severity"] == "error"
        assert error["line"] is not None
        assert "message" in error

    def test_unclosed_html_tag(self, authed_client, team_with_users):
        response = authed_client.post(
            self._url(team_with_users),
            data=json.dumps({"template": "<div><p>{{ foo }}</div>"}),
            content_type="application/json",
        )
        assert response.status_code == 200
        data = response.json()
        # Should have HTML lint warning (H025 orphan tag)
        warnings = [e for e in data["errors"] if e["severity"] == "warning"]
        assert len(warnings) >= 1
        assert any("H025" in w["message"] for w in warnings)

    def test_valid_html_no_warnings(self, authed_client, team_with_users):
        response = authed_client.post(
            self._url(team_with_users),
            data=json.dumps({"template": "<div><p>{{ foo }}</p></div>"}),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json()["errors"] == []

    def test_missing_template_field(self, authed_client, team_with_users):
        response = authed_client.post(
            self._url(team_with_users),
            data=json.dumps({}),
            content_type="application/json",
        )
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
import tempfile
from pathlib import Path

from djlint.lint import lint_file
from djlint.settings import Config as DjlintConfig


# Curated djlint rules relevant to template fragments
DJLINT_ALLOWED_RULES = {"H020", "H021", "H025", "T027", "T034"}


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

    errors = []

    # 1. Jinja syntax validation
    if template:
        try:
            SandboxedEnvironment().parse(template)
        except TemplateSyntaxError as e:
            errors.append({
                "line": e.lineno or 1,
                "column": 0,
                "message": e.message,
                "severity": "error",
            })

    # 2. HTML linting via djlint (only if no Jinja syntax errors)
    if not errors and template:
        errors.extend(_djlint_check(template))

    return JsonResponse({"errors": errors})


def _djlint_check(template: str) -> list[dict]:
    """Run djlint on a template string and return lint issues as dicts."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False) as f:
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

Add the required imports at the top of `views.py`:

```python
import tempfile
from pathlib import Path

from djlint.lint import lint_file
from djlint.settings import Config as DjlintConfig
from jinja2 import TemplateSyntaxError
from jinja2.sandbox import SandboxedEnvironment
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
  public async validateJinja(template: string): Promise<{errors: Array<{line: number, column: number, message: string, severity: string}>}> {
    return this.makeRequest<{errors: Array<{line: number, column: number, message: string, severity: string}>}>(
      "post",
      `/pipelines/validate-jinja/`,
      { template },
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

### Task 8: Add linter extension to JinjaEditor

**Files:**
- Modify: `assets/javascript/apps/pipeline/components/CodeMirrorEditor.tsx`

- [ ] **Step 1: Add linter to JinjaEditor**

In `assets/javascript/apps/pipeline/components/CodeMirrorEditor.tsx`, add the import:

```typescript
import {linter, Diagnostic} from "@codemirror/lint";
import {apiClient} from "../api/api";
```

Then replace the `JinjaEditor` function (currently at line 259) with:

```typescript
function jinjaLinter() {
  return linter(async (view): Promise<Diagnostic[]> => {
    const doc = view.state.doc.toString();
    if (!doc.trim()) return [];

    try {
      const result = await apiClient.validateJinja(doc);
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
  {value, onChange, readOnly, autocompleteVars}: {
    value: string;
    onChange: (value: string) => void;
    readOnly: boolean;
    autocompleteVars: string[];
  }
) {
  const jinjaVariables = autocompleteVars.map((v) => ({ label: v, type: "variable" }));
  let extensions = [
    jinja({ variables: jinjaVariables }),
    jinjaLinter(),
    EditorView.lineWrapping,
  ];
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

- [ ] **Step 2: Lint and type-check**

Run: `npm run lint assets/javascript/apps/pipeline/components/CodeMirrorEditor.tsx -- --fix`
Run: `npm run type-check`

- [ ] **Step 3: Build frontend**

Run: `npm run dev`
Expected: Build succeeds with no errors

- [ ] **Step 4: Commit**

```bash
git add assets/javascript/apps/pipeline/components/CodeMirrorEditor.tsx
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
