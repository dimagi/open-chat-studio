# Dynamic Send Email Node — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make `SendEmail`'s `subject` and `recipient_list` fields support Python format-string interpolation from pipeline context, and add an optional Jinja2 `body` template field.

**Architecture:** Extend `SendEmail` in `nodes.py` to render `subject`/`recipient_list` at runtime using `PromptTemplateContext` + `str.format()` (same pattern as routing/LLM prompt nodes), and add an optional `body` field rendered with Jinja2 `SandboxedEnvironment` (same as `RenderTemplate`). Extract shared Jinja2 context-building into a module-level helper used by both `RenderTemplate` and `SendEmail`.

**Tech Stack:** Python 3.13+, Pydantic v2, Jinja2 `SandboxedEnvironment`, `PromptTemplateContext`, `SafeAccessWrapper`, Celery, pytest + `pytest-django`.

---

### Task 1: Write failing tests for updated `recipient_list` validator

`recipient_list` with template syntax (`{participant_data.email}`) should pass construction-time validation, but currently fails.

**Files:**
- Modify: `apps/pipelines/tests/test_nodes.py`

**Step 1: Add template-syntax cases to the valid parametrize list**

Open `apps/pipelines/tests/test_nodes.py`. Find `TestSendEmailInputValidation.test_valid_recipient_list` (line ~43). Add two template-syntax strings to the parametrize list:

```python
@pytest.mark.parametrize(
    "recipient_list",
    [
        "test@example.com",
        "test@example.com,another@example.com",
        "test@example.com,another@example.com,yetanother@example.com",
        "{participant_data.email}",                         # single template
        "{participant_data.email},{temp_state.cc_email}",  # multiple templates
    ],
)
def test_valid_recipient_list(self, recipient_list):
    model = SendEmail(
        node_id="test", django_node=None, name="email", recipient_list=recipient_list, subject="Test Subject"
    )
    assert model.recipient_list == recipient_list
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest apps/pipelines/tests/test_nodes.py::TestSendEmailInputValidation::test_valid_recipient_list -v
```

Expected: Two new parametrize cases FAIL with `ValidationError: Invalid list of emails addresses`.

**Step 3: Update validator in `apps/pipelines/nodes/nodes.py`**

Find `recipient_list_has_valid_emails` (~line 434). Replace the validator body to skip validation when the value looks like a format string:

```python
@field_validator("recipient_list", mode="before")
def recipient_list_has_valid_emails(cls, value):
    value = value or ""
    if "{" in value:
        return value  # template syntax — validate at runtime after rendering
    for email in [email.strip() for email in value.split(",")]:
        try:
            validate_email(email)
        except ValidationError:
            raise PydanticCustomError("invalid_recipient_list", "Invalid list of emails addresses") from None
    return value
```

**Step 4: Run tests to verify they pass**

```bash
uv run pytest apps/pipelines/tests/test_nodes.py::TestSendEmailInputValidation -v
```

Expected: ALL cases PASS (existing + new template cases).

**Step 5: Commit**

```bash
git add apps/pipelines/tests/test_nodes.py apps/pipelines/nodes/nodes.py
git commit -m "feat: allow template syntax in SendEmail recipient_list field"
```

---

### Task 2: Add optional `body` field to `SendEmail`

**Files:**
- Modify: `apps/pipelines/nodes/nodes.py`
- Modify: `apps/pipelines/tests/test_nodes.py`

**Step 1: Write a failing test for the `body` field**

In `test_nodes.py`, add to `TestSendEmailInputValidation`:

```python
def test_body_field_defaults_to_empty(self):
    model = SendEmail(
        node_id="test", django_node=None, name="email",
        recipient_list="test@example.com", subject="Hello"
    )
    assert model.body == ""

def test_body_field_accepts_template(self):
    model = SendEmail(
        node_id="test", django_node=None, name="email",
        recipient_list="test@example.com", subject="Hello",
        body="Dear {{participant_data.name}}, your input was: {{input}}"
    )
    assert "participant_data.name" in model.body
```

**Step 2: Run tests to verify they fail**

```bash
uv run pytest apps/pipelines/tests/test_nodes.py::TestSendEmailInputValidation::test_body_field_defaults_to_empty apps/pipelines/tests/test_nodes.py::TestSendEmailInputValidation::test_body_field_accepts_template -v
```

Expected: FAIL with `ValidationError` (unknown field).

**Step 3: Add `body` field to `SendEmail` in `nodes.py`**

Find the `SendEmail` class. Update the field declarations (after `subject: str`):

```python
recipient_list: str = Field(
    description=(
        "A comma-separated list of email addresses. "
        "Supports Python format strings, e.g. {participant_data.email}"
    )
)
subject: str = Field(
    description="Email subject. Supports Python format strings, e.g. {participant_data.name}"
)
body: str = Field(
    default="",
    description=(
        "Optional Jinja2 template for the email body. "
        "If empty, the pipeline input is used. "
        "Available variables: input, temp_state, session_state, participant_data, participant_details."
    ),
    json_schema_extra=UiSchema(widget=Widgets.expandable_text),
)
```

**Step 4: Run tests to verify they pass**

```bash
uv run pytest apps/pipelines/tests/test_nodes.py::TestSendEmailInputValidation -v
```

Expected: ALL PASS.

**Step 5: Commit**

```bash
git add apps/pipelines/nodes/nodes.py apps/pipelines/tests/test_nodes.py
git commit -m "feat: add optional body template field to SendEmail node"
```

---

### Task 3: Extract shared Jinja2 context builder and update `RenderTemplate`

`RenderTemplate._process` builds a context dict for Jinja2. `SendEmail` will need the same dict. Extract it so both can share it.

**Files:**
- Modify: `apps/pipelines/nodes/nodes.py`

**Step 1: Add module-level helper function**

Add `from string import Formatter` to the import block at the top of `nodes.py` (after the existing `import re` line):

```python
from string import Formatter
```

Then, just above the `RenderTemplate` class definition (~line 97), add:

```python
def _build_jinja_context(context: "NodeContext", repo) -> dict:
    """Build the Jinja2 template context dict shared by RenderTemplate and SendEmail."""
    content = {
        "input": context.input,
        "node_inputs": context.inputs,
        "temp_state": context.state.temp,
        "session_state": context.state.session_state,
        "input_message_id": context.input_message_id,
        "input_message_url": context.input_message_url,
    }
    session = context.session
    if session:
        participant = repo.participant
        if participant:
            content.update(
                {
                    "participant_details": {
                        "identifier": getattr(participant, "identifier", None),
                        "platform": getattr(participant, "platform", None),
                    },
                    "participant_schedules": repo.get_participant_schedules(
                        as_dict=True,
                        include_inactive=True,
                    )
                    or [],
                }
            )
        content["participant_data"] = context.state.participant_data
    return content
```

**Step 2: Update `RenderTemplate._process` to use the helper**

Replace the inline context-building in `RenderTemplate._process` with a call to `_build_jinja_context`:

```python
def _process(self, state: PipelineState, context: "NodeContext") -> PipelineState:
    env = SandboxedEnvironment()
    try:
        content = _build_jinja_context(context, self.repo)
        template = env.from_string(self.template_string)
        output = template.render(content)
    except Exception as e:
        raise PipelineNodeRunError(f"Error rendering template: {e}") from e

    return PipelineState.from_node_output(node_name=self.name, node_id=self.node_id, output=output)
```

**Step 3: Run the existing template node test to confirm no regression**

```bash
uv run pytest apps/pipelines/tests/test_template_node.py -v
```

Expected: PASS.

**Step 4: Commit**

```bash
git add apps/pipelines/nodes/nodes.py
git commit -m "refactor: extract _build_jinja_context helper shared by RenderTemplate and SendEmail"
```

---

### Task 4: Write failing integration tests for dynamic `SendEmail._process`

Before implementing dynamic rendering, write the tests that will verify it works.

**Files:**
- Modify: `apps/pipelines/tests/test_nodes.py`

**Step 1: Add imports at the top of `test_nodes.py`**

```python
from unittest.mock import patch

import pytest

from apps.experiments.models import Participant
from apps.pipelines.nodes.base import PipelineState
from apps.pipelines.repository import ORMRepository
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.pipelines import PipelineFactory
```

(Check which are already imported — add only what's missing.)

**Step 2: Add a new test class for dynamic rendering**

Add this class to `test_nodes.py`:

```python
@pytest.mark.django_db()
class TestSendEmailDynamicRendering:
    @pytest.fixture()
    def experiment_session(self):
        return ExperimentSessionFactory.create()

    @pytest.fixture()
    def participant(self, experiment_session):
        p = Participant.objects.create(
            identifier="user_001",
            team=experiment_session.team,
            platform="web",
        )
        experiment_session.participant = p
        experiment_session.save()
        return p

    def _make_state(self, experiment_session, participant_data=None, temp_state=None):
        return PipelineState(
            experiment_session=experiment_session,
            messages=["hello"],
            temp_state=temp_state or {},
            outputs={},
            participant_data=participant_data or {},
        )

    def _make_node(self, recipient_list, subject, body=""):
        return SendEmail(
            node_id="email-1",
            django_node=None,
            name="email",
            recipient_list=recipient_list,
            subject=subject,
            body=body,
        )

    def _run_node(self, node, state, experiment_session):
        config = {"configurable": {"repo": ORMRepository(session=experiment_session)}}
        with patch("apps.pipelines.nodes.nodes.send_email_from_pipeline") as mock_task:
            node.process(incoming_nodes=[], outgoing_nodes=[], state=state, config=config)
            return mock_task

    def test_static_email_still_works(self, experiment_session, participant):
        """Existing behaviour: static recipient/subject, body from input."""
        node = self._make_node("ops@example.com", "Weekly Report")
        state = self._make_state(experiment_session)
        mock_task = self._run_node(node, state, experiment_session)
        mock_task.delay.assert_called_once_with(
            recipient_list=["ops@example.com"],
            subject="Weekly Report",
            message="hello",
        )

    def test_dynamic_subject_from_participant_data(self, experiment_session, participant):
        node = self._make_node(
            recipient_list="ops@example.com",
            subject="Hello {participant_data.name}",
        )
        state = self._make_state(experiment_session, participant_data={"name": "Alice"})
        mock_task = self._run_node(node, state, experiment_session)
        mock_task.delay.assert_called_once_with(
            recipient_list=["ops@example.com"],
            subject="Hello Alice",
            message="hello",
        )

    def test_dynamic_recipient_from_temp_state(self, experiment_session, participant):
        node = self._make_node(
            recipient_list="{temp_state.email_to}",
            subject="Notification",
        )
        state = self._make_state(experiment_session, temp_state={"email_to": "bob@example.com"})
        mock_task = self._run_node(node, state, experiment_session)
        mock_task.delay.assert_called_once_with(
            recipient_list=["bob@example.com"],
            subject="Notification",
            message="hello",
        )

    def test_invalid_email_after_rendering_raises_error(self, experiment_session, participant):
        from apps.pipelines.exceptions import PipelineNodeRunError
        node = self._make_node(
            recipient_list="{temp_state.bad_email}",
            subject="Hi",
        )
        state = self._make_state(experiment_session, temp_state={"bad_email": "not-an-email"})
        config = {"configurable": {"repo": ORMRepository(session=experiment_session)}}
        with pytest.raises(PipelineNodeRunError, match="Invalid email address"):
            node.process(incoming_nodes=[], outgoing_nodes=[], state=state, config=config)

    def test_body_template_renders(self, experiment_session, participant):
        node = self._make_node(
            recipient_list="ops@example.com",
            subject="Report",
            body="Input was: {{input}}. Name: {{participant_data.name}}",
        )
        state = self._make_state(experiment_session, participant_data={"name": "Carol"})
        mock_task = self._run_node(node, state, experiment_session)
        mock_task.delay.assert_called_once_with(
            recipient_list=["ops@example.com"],
            subject="Report",
            message="Input was: hello. Name: Carol",
        )

    def test_empty_body_defaults_to_input(self, experiment_session, participant):
        node = self._make_node("ops@example.com", "Report", body="")
        state = self._make_state(experiment_session)
        mock_task = self._run_node(node, state, experiment_session)
        mock_task.delay.assert_called_once_with(
            recipient_list=["ops@example.com"],
            subject="Report",
            message="hello",
        )
```

**Step 3: Run tests to verify they fail (except `test_static_email_still_works` which may pass)**

```bash
uv run pytest apps/pipelines/tests/test_nodes.py::TestSendEmailDynamicRendering -v
```

Expected: Most FAIL because `_process` doesn't render templates yet.

**Step 4: Commit the failing tests**

```bash
git add apps/pipelines/tests/test_nodes.py
git commit -m "test: add failing tests for dynamic SendEmail rendering"
```

---

### Task 5: Implement dynamic rendering in `SendEmail._process`

**Files:**
- Modify: `apps/pipelines/nodes/nodes.py`

**Step 1: Replace `_process` in `SendEmail`**

Find `SendEmail._process` (~line 444). Replace it entirely:

```python
def _process(self, state: PipelineState, context: "NodeContext") -> PipelineState:
    extra = {
        "input": context.input,
        "temp_state": context.state.temp or {},
        "session_state": context.state.session_state or {},
    }
    template_context = PromptTemplateContext(
        session=context.session,
        extra=extra,
        participant_data=context.state.participant_data or {},
        repo=self.repo,
    )

    def render_format_string(template: str) -> str:
        input_vars = {v for _, v, _, _ in Formatter().parse(template) if v is not None}
        ctx = template_context.get_context(input_vars)
        try:
            return template.format(**ctx)
        except KeyError as e:
            raise PipelineNodeRunError(f"Unknown variable in email template: {e}") from e

    subject = render_format_string(self.subject)
    rendered_recipients = render_format_string(self.recipient_list)

    recipients = [r.strip() for r in rendered_recipients.split(",")]
    for email in recipients:
        try:
            validate_email(email)
        except ValidationError:
            raise PipelineNodeRunError(f"Invalid email address after rendering: {email!r}")

    if self.body:
        env = SandboxedEnvironment()
        try:
            message = env.from_string(self.body).render(_build_jinja_context(context, self.repo))
        except Exception as e:
            raise PipelineNodeRunError(f"Error rendering email body: {e}") from e
    else:
        message = context.input

    send_email_from_pipeline.delay(recipient_list=recipients, subject=subject, message=message)
    return PipelineState.from_node_output(node_name=self.name, node_id=self.node_id, output=context.input)
```

**Step 2: Run all `SendEmail` tests**

```bash
uv run pytest apps/pipelines/tests/test_nodes.py::TestSendEmailInputValidation apps/pipelines/tests/test_nodes.py::TestSendEmailDynamicRendering -v
```

Expected: ALL PASS.

**Step 3: Run full pipeline test suite to catch regressions**

```bash
uv run pytest apps/pipelines/tests/ -v
```

Expected: ALL PASS.

**Step 4: Lint and type-check**

```bash
uv run ruff check apps/pipelines/nodes/nodes.py apps/pipelines/tests/test_nodes.py --fix
uv run ruff format apps/pipelines/nodes/nodes.py apps/pipelines/tests/test_nodes.py
uv run ty check apps/pipelines/nodes/nodes.py
```

Fix any issues reported.

**Step 5: Commit**

```bash
git add apps/pipelines/nodes/nodes.py
git commit -m "feat: render subject, recipient_list and body dynamically in SendEmail node"
```

---

### Task 6: Update `email_node` test utility

**Files:**
- Modify: `apps/pipelines/tests/utils.py`

**Step 1: Update `email_node()` helper to accept an optional `body` parameter**

Find `email_node()` (~line 89) in `utils.py`. Update it:

```python
def email_node(name: str | None = None, body: str = ""):
    node = _with_node_id_and_name(
        name,
        "send_email",
        {
            "label": "Send an email",
            "type": "SendEmail",
            "params": {
                "recipient_list": "test@example.com",
                "subject": "This is an interesting email",
                "body": body,
            },
        },
    )
    return node
```

**Step 2: Run tests that use `email_node()` to confirm no breakage**

```bash
uv run pytest apps/pipelines/tests/ -k "email" -v
```

Expected: ALL PASS.

**Step 3: Commit**

```bash
git add apps/pipelines/tests/utils.py
git commit -m "feat: update email_node test utility to support optional body parameter"
```

---

### Task 7: Final verification

**Step 1: Run full pipeline test suite**

```bash
uv run pytest apps/pipelines/ -v
```

Expected: ALL PASS.

**Step 2: Lint and type-check everything touched**

```bash
uv run ruff check apps/pipelines/nodes/nodes.py apps/pipelines/tests/test_nodes.py apps/pipelines/tests/utils.py --fix
uv run ruff format apps/pipelines/nodes/nodes.py apps/pipelines/tests/test_nodes.py apps/pipelines/tests/utils.py
uv run ty check apps/pipelines/
```

Expected: No errors.

**Step 3: Done — move to PR**

Use `superpowers:finishing-a-development-branch` or the `commit-commands:commit-push-pr` skill to open a PR.
