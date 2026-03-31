# Session Mode for Evaluation Datasets — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "session" evaluation mode so one dataset item represents an entire conversation session, and evaluators fire once per session instead of once per message pair.

**Architecture:** Add an `evaluation_mode` field to `EvaluationDataset` and `Evaluator` models. Create a new `make_session_evaluation_message()` function that produces one `EvaluationMessage` per session (empty `input`/`output`, full transcript in `history`). Wire up form validation on `EvaluationConfigForm` to enforce mode matching, and use Alpine.js to conditionally show/hide UI elements.

**Tech Stack:** Django, Celery, Alpine.js, HTMX, pytest, FactoryBoy

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `apps/evaluations/models.py` | Add `evaluation_mode` to `EvaluationDataset` and `Evaluator`; update `__str__`, `as_result_dict` |
| Create | `apps/evaluations/migrations/0011_add_evaluation_mode.py` | Migration for new fields |
| Modify | `apps/evaluations/utils.py` | New `make_session_evaluation_message()` function |
| Modify | `apps/evaluations/forms.py` | Add `evaluation_mode` to dataset/evaluator forms; eval config validation |
| Modify | `apps/evaluations/tasks.py` | New Celery task for session-mode clone; adapt dedup logic |
| Modify | `apps/utils/factories/evaluations.py` | Add `evaluation_mode` to factories |
| Modify | `templates/evaluations/dataset_create_form.html` | Evaluation mode selector; hide manual/CSV for session mode |
| Modify | `templates/evaluations/evaluator_form.html` | Mode selector; dynamic autocomplete vars |
| Modify | `templates/evaluations/evaluation_config_form.html` | Hide generation experiment for session-mode datasets |
| Create | `apps/evaluations/tests/test_session_mode.py` | All session-mode tests |

---

### Task 1: Add `evaluation_mode` field to models

**Files:**
- Modify: `apps/evaluations/models.py:55-79` (Evaluator)
- Modify: `apps/evaluations/models.py:201-237` (EvaluationDataset)

- [ ] **Step 1: Add `EvaluationMode` choices and model fields**

In `apps/evaluations/models.py`, add the choices class near the other enums (around line 28) and the fields to both models:

```python
# Add near other enums (after line 53)
class EvaluationMode(models.TextChoices):
    MESSAGE = "message", "Message"
    SESSION = "session", "Session"
```

Add to `Evaluator` (after `params` field, around line 60):

```python
evaluation_mode = models.CharField(
    max_length=10,
    choices=EvaluationMode.choices,
    default=EvaluationMode.MESSAGE,
    help_text="Message mode evaluates individual message pairs; Session mode evaluates entire conversations",
)
```

Add to `EvaluationDataset` (after `name` field, around line 203):

```python
evaluation_mode = models.CharField(
    max_length=10,
    choices=EvaluationMode.choices,
    default=EvaluationMode.MESSAGE,
    help_text="Message mode stores individual message pairs; Session mode stores entire conversations",
)
```

- [ ] **Step 2: Create and run migration**

Run:
```bash
uv run python manage.py makemigrations evaluations -n add_evaluation_mode
```

Then run:
```bash
uv run python manage.py migrate
```

Expected: Migration created and applied successfully.

- [ ] **Step 3: Commit**

```bash
git add apps/evaluations/models.py apps/evaluations/migrations/0011_add_evaluation_mode.py
git commit -m "feat: add evaluation_mode field to EvaluationDataset and Evaluator models"
```

---

### Task 2: Update `EvaluationMessage.__str__` and `as_result_dict`

**Files:**
- Modify: `apps/evaluations/models.py:108-198`
- Test: `apps/evaluations/tests/test_session_mode.py`

- [ ] **Step 1: Write failing tests for `__str__` and `as_result_dict`**

Create `apps/evaluations/tests/test_session_mode.py`:

```python
import pytest

from apps.evaluations.models import EvaluationMessage


@pytest.mark.django_db()
class TestSessionModeEvaluationMessage:
    def test_str_with_empty_input_output(self):
        """Session-mode messages have empty input/output dicts."""
        msg = EvaluationMessage(
            input={},
            output={},
            history=[
                {"message_type": "human", "content": "Hello there"},
                {"message_type": "ai", "content": "Hi!"},
            ],
        )
        result = str(msg)
        assert result == "Session evaluation"

    def test_str_with_normal_input_output(self):
        """Message-mode messages should still work as before."""
        msg = EvaluationMessage(
            input={"content": "Hello", "role": "human"},
            output={"content": "Hi!", "role": "ai"},
        )
        result = str(msg)
        assert "Hello" in result
        assert "Hi!" in result

    def test_as_result_dict_includes_participant_data_and_session_state(self):
        """as_result_dict should include participant_data and session_state."""
        msg = EvaluationMessage(
            input={"content": "Hello", "role": "human"},
            output={"content": "Hi!", "role": "ai"},
            context={"current_datetime": "2025-01-01"},
            history=[],
            metadata={"session_id": "abc"},
            participant_data={"name": "John"},
            session_state={"step": 1},
        )
        result = msg.as_result_dict()
        assert result["participant_data"] == {"name": "John"}
        assert result["session_state"] == {"step": 1}
        assert result["input"] == {"content": "Hello", "role": "human"}
        assert result["output"] == {"content": "Hi!", "role": "ai"}
        assert result["context"] == {"current_datetime": "2025-01-01"}
        assert result["history"] == []
        assert result["metadata"] == {"session_id": "abc"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/evaluations/tests/test_session_mode.py -v`

Expected: `test_str_with_empty_input_output` fails (shows "(Human): no content, (Ai): no content" instead of "Session evaluation"). `test_as_result_dict_includes_participant_data_and_session_state` fails (missing keys).

- [ ] **Step 3: Update `__str__` to handle empty input/output**

In `apps/evaluations/models.py`, replace `__str__` (lines 108-113):

```python
def __str__(self):
    if not self.input and not self.output:
        return "Session evaluation"
    input_role = self.input.get("role", "(human)").title()
    input_content = self.input.get("content", "no content")
    output_role = self.output.get("role", "(ai)").title()
    output_content = self.output.get("content", "no content")
    return f"{input_role}: {input_content}, {output_role}: {output_content}"
```

- [ ] **Step 4: Update `as_result_dict` to include `participant_data` and `session_state`**

In `apps/evaluations/models.py`, replace `as_result_dict` (lines 190-198):

```python
def as_result_dict(self) -> dict:
    """Returns a dict representation to be stored in any evaluator result"""
    return {
        "input": self.input,
        "output": self.output,
        "context": self.context,
        "history": self.history,
        "metadata": self.metadata,
        "participant_data": self.participant_data,
        "session_state": self.session_state,
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest apps/evaluations/tests/test_session_mode.py -v`

Expected: All 3 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/evaluations/models.py apps/evaluations/tests/test_session_mode.py
git commit -m "feat: update EvaluationMessage __str__ and as_result_dict for session mode"
```

---

### Task 3: Update factories

**Files:**
- Modify: `apps/utils/factories/evaluations.py`

- [ ] **Step 1: Add `evaluation_mode` to `EvaluatorFactory` and `EvaluationDatasetFactory`**

In `apps/utils/factories/evaluations.py`:

Add to `EvaluatorFactory` (after `type = "LLM"` on line 22):

```python
name = factory.Sequence(lambda n: f"Test Evaluator {n}")
evaluation_mode = "message"
```

Add to `EvaluationDatasetFactory` (after `name` field, around line 62):

```python
evaluation_mode = "message"
```

- [ ] **Step 2: Run existing tests to verify nothing broke**

Run: `uv run pytest apps/evaluations/tests/ -v --timeout=30`

Expected: All existing tests still pass.

- [ ] **Step 3: Commit**

```bash
git add apps/utils/factories/evaluations.py
git commit -m "feat: add evaluation_mode to evaluation factories"
```

---

### Task 4: Create `make_session_evaluation_message` function

**Files:**
- Modify: `apps/evaluations/utils.py`
- Test: `apps/evaluations/tests/test_session_mode.py`

- [ ] **Step 1: Write failing tests for session-mode message creation**

Add to `apps/evaluations/tests/test_session_mode.py`:

```python
from apps.chat.models import ChatMessageType
from apps.evaluations.utils import make_session_evaluation_messages
from apps.utils.factories.experiment import ChatMessageFactory, ExperimentSessionFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.traces import TraceFactory


@pytest.mark.django_db()
class TestMakeSessionEvaluationMessages:
    def test_happy_path_multi_turn_session(self):
        """Session with N turns produces one EvaluationMessage with full history."""
        team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team)
        chat = session.chat

        human_1 = ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Hello")
        ai_1 = ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.AI, content="Hi there!")
        human_2 = ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.HUMAN, content="How are you?")

        # Add trace with participant_data and session_state on the AI message
        TraceFactory.create(
            team=team,
            experiment=session.experiment,
            session=session,
            participant=session.participant,
            input_message=ai_1,
            duration=100,
            participant_data={"name": "Alice"},
            session_state={"step": 2},
        )

        ai_2 = ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.AI, content="I'm doing well!")
        TraceFactory.create(
            team=team,
            experiment=session.experiment,
            session=session,
            participant=session.participant,
            input_message=ai_2,
            duration=100,
            participant_data={"name": "Alice", "visits": 3},
            session_state={"step": 3},
        )

        result = make_session_evaluation_messages([session.external_id])

        assert len(result) == 1
        msg = result[0]
        assert msg.input == {}
        assert msg.output == {}
        assert len(msg.history) == 4
        assert msg.history[0]["message_type"] == ChatMessageType.HUMAN
        assert msg.history[0]["content"] == "Hello"
        assert msg.history[1]["message_type"] == ChatMessageType.AI
        assert msg.history[1]["content"] == "Hi there!"
        assert msg.history[2]["message_type"] == ChatMessageType.HUMAN
        assert msg.history[2]["content"] == "How are you?"
        assert msg.history[3]["message_type"] == ChatMessageType.AI
        assert msg.history[3]["content"] == "I'm doing well!"
        # participant_data and session_state from last AI message's trace
        assert msg.participant_data == {"name": "Alice", "visits": 3}
        assert msg.session_state == {"step": 3}
        assert msg.metadata["session_id"] == session.external_id
        assert msg.metadata["experiment_id"] == str(session.experiment.public_id)
        assert msg.metadata["created_mode"] == "clone"
        assert msg.input_chat_message is None
        assert msg.expected_output_chat_message is None

    def test_single_turn_session(self):
        """Session with one human-AI pair still produces one message."""
        team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team)
        chat = session.chat

        ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Hello")
        ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.AI, content="Hi!")

        result = make_session_evaluation_messages([session.external_id])

        assert len(result) == 1
        assert len(result[0].history) == 2

    def test_orphaned_last_human_message(self):
        """Session ending with human message (no AI response)."""
        team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team)
        chat = session.chat

        ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Hello")
        ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.AI, content="Hi!")
        ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Bye")

        result = make_session_evaluation_messages([session.external_id])

        assert len(result) == 1
        assert len(result[0].history) == 3
        assert result[0].input == {}
        assert result[0].output == {}
        assert result[0].participant_data == {}
        assert result[0].session_state == {}

    def test_human_only_session(self):
        """Session with only human messages."""
        team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team)
        chat = session.chat

        ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Hello")
        ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Anyone there?")

        result = make_session_evaluation_messages([session.external_id])

        assert len(result) == 1
        assert len(result[0].history) == 2
        assert result[0].participant_data == {}
        assert result[0].session_state == {}

    def test_empty_session(self):
        """Session with no messages produces no EvaluationMessage."""
        team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team)

        result = make_session_evaluation_messages([session.external_id])

        assert len(result) == 0

    def test_participant_data_from_last_ai_trace(self):
        """Verify participant_data and session_state come from last AI message's trace."""
        team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team)
        chat = session.chat

        human_1 = ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Hello")
        ai_1 = ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.AI, content="Hi!")
        TraceFactory.create(
            team=team,
            experiment=session.experiment,
            session=session,
            participant=session.participant,
            input_message=ai_1,
            duration=100,
            participant_data={"version": 1},
            session_state={"first": True},
        )

        human_2 = ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.HUMAN, content="More")
        ai_2 = ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.AI, content="Sure!")
        TraceFactory.create(
            team=team,
            experiment=session.experiment,
            session=session,
            participant=session.participant,
            input_message=ai_2,
            duration=100,
            participant_data={"version": 2},
            session_state={"first": False},
        )

        result = make_session_evaluation_messages([session.external_id])

        assert result[0].participant_data == {"version": 2}
        assert result[0].session_state == {"first": False}

    def test_multiple_sessions(self):
        """Multiple sessions produce one EvaluationMessage each."""
        team = TeamFactory.create()
        session_1 = ExperimentSessionFactory.create(team=team)
        session_2 = ExperimentSessionFactory.create(team=team)

        ChatMessageFactory.create(chat=session_1.chat, message_type=ChatMessageType.HUMAN, content="S1 Hello")
        ChatMessageFactory.create(chat=session_1.chat, message_type=ChatMessageType.AI, content="S1 Hi!")
        ChatMessageFactory.create(chat=session_2.chat, message_type=ChatMessageType.HUMAN, content="S2 Hello")
        ChatMessageFactory.create(chat=session_2.chat, message_type=ChatMessageType.AI, content="S2 Hi!")

        result = make_session_evaluation_messages([session_1.external_id, session_2.external_id])

        assert len(result) == 2
        session_ids = {msg.metadata["session_id"] for msg in result}
        assert session_1.external_id in session_ids
        assert session_2.external_id in session_ids

    def test_metadata_structure(self):
        """Verify metadata has session_id, experiment_id, and created_mode."""
        team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team)
        chat = session.chat

        ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Hello")
        ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.AI, content="Hi!")

        result = make_session_evaluation_messages([session.external_id])

        metadata = result[0].metadata
        assert metadata["session_id"] == session.external_id
        assert metadata["experiment_id"] == str(session.experiment.public_id)
        assert metadata["created_mode"] == "clone"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/evaluations/tests/test_session_mode.py::TestMakeSessionEvaluationMessages -v`

Expected: FAIL — `ImportError` because `make_session_evaluation_messages` doesn't exist yet.

- [ ] **Step 3: Implement `make_session_evaluation_messages`**

Add to `apps/evaluations/utils.py` (before `make_evaluation_messages_from_sessions`, around line 216):

```python
def make_session_evaluation_messages(session_external_ids: list[str]) -> list["EvaluationMessage"]:
    """Create one EvaluationMessage per session, with the full conversation as history.

    Unlike make_evaluation_messages_from_sessions (which creates one message per human-AI pair),
    this creates a single message per session for holistic session evaluation.
    """
    from apps.evaluations.models import EvaluationMessage  # noqa: PLC0415

    if not session_external_ids:
        return []

    all_messages = list(
        ChatMessage.objects.filter(
            chat__experiment_session__external_id__in=session_external_ids,
        )
        .annotate(
            session_external_id=F("chat__experiment_session__external_id"),
            experiment_public_id=F("chat__experiment_session__experiment__public_id"),
            trace_participant_data=F("input_message_trace__participant_data"),
            trace_session_state=F("input_message_trace__session_state"),
        )
        .order_by("chat__experiment_session__created_at", "created_at")
    )

    # Group messages by session
    sessions: dict[str, list] = {}
    for msg in all_messages:
        sessions.setdefault(msg.session_external_id, []).append(msg)

    result = []
    for session_id, messages in sessions.items():
        history = [
            {
                "message_type": msg.message_type,
                "content": msg.content,
                "summary": getattr(msg, "summary", None),
            }
            for msg in messages
        ]

        # Get participant_data and session_state from the last AI message's trace
        participant_data = {}
        session_state = {}
        for msg in reversed(messages):
            if msg.message_type == ChatMessageType.AI:
                participant_data = msg.trace_participant_data or {}
                session_state = msg.trace_session_state or {}
                break

        eval_message = EvaluationMessage(
            input={},
            output={},
            history=history,
            participant_data=participant_data,
            session_state=session_state,
            metadata={
                "session_id": session_id,
                "experiment_id": str(messages[0].experiment_public_id),
                "created_mode": "clone",
            },
            input_chat_message=None,
            expected_output_chat_message=None,
        )
        result.append(eval_message)

    return result
```

Make sure `ChatMessage` and `ChatMessageType` imports exist at the top of utils.py. The existing function already imports `ChatMessage` — verify by checking the imports at the top of the file. Also ensure `F` is imported from `django.db.models`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/evaluations/tests/test_session_mode.py::TestMakeSessionEvaluationMessages -v`

Expected: All 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/evaluations/utils.py apps/evaluations/tests/test_session_mode.py
git commit -m "feat: add make_session_evaluation_messages for session-mode dataset creation"
```

---

### Task 5: Add Celery task for session-mode clone

**Files:**
- Modify: `apps/evaluations/tasks.py`

- [ ] **Step 1: Add `create_session_mode_dataset_task`**

Add a new Celery task in `apps/evaluations/tasks.py` (after `create_dataset_from_sessions_task`, around line 885):

```python
@shared_task(bind=True, base=TaskbadgerTask)
def create_session_mode_dataset_task(self, dataset_id, team_id, session_ids):
    """
    Create session-mode evaluation messages from sessions asynchronously.

    Each session becomes one EvaluationMessage with the full conversation as history.

    Args:
        dataset_id: ID of the EvaluationDataset to populate
        team_id: ID of the team
        session_ids: List of session external IDs
    """
    from apps.evaluations.utils import make_session_evaluation_messages  # noqa: PLC0415

    progress_recorder = ProgressRecorder(self)
    dataset = None

    try:
        dataset = EvaluationDataset.objects.select_related("team").get(id=dataset_id, team_id=team_id)
    except EvaluationDataset.DoesNotExist:
        logger.error(f"Dataset {dataset_id} not found for team {team_id}")
        return {"success": False, "error": "Dataset not found"}

    dataset.status = DatasetCreationStatus.PROCESSING
    dataset.save(update_fields=["status"])

    try:
        progress_recorder.set_progress(0, 100, "Starting session-mode clone...")

        with current_team(dataset.team):
            evaluation_messages = make_session_evaluation_messages(session_ids)

            progress_recorder.set_progress(
                40, 100, f"Found {len(evaluation_messages)} sessions, checking for duplicates..."
            )

            # Deduplicate by metadata.session_id
            existing_session_ids = set(
                dataset.messages.values_list("metadata__session_id", flat=True)
            )

            messages_to_add = [
                msg for msg in evaluation_messages
                if msg.metadata.get("session_id") not in existing_session_ids
            ]

            if not messages_to_add:
                dataset.status = DatasetCreationStatus.COMPLETED
                dataset.job_id = ""
                dataset.save(update_fields=["status", "job_id"])
                progress_recorder.set_progress(100, 100, "Clone complete - no new sessions to add")
                return {"success": True, "created_count": 0, "duplicates_skipped": len(evaluation_messages)}

            progress_recorder.set_progress(70, 100, f"Creating {len(messages_to_add)} new session messages...")

            created_messages = EvaluationMessage.objects.bulk_create(messages_to_add)
            dataset.messages.add(*created_messages)

            dataset.status = DatasetCreationStatus.COMPLETED
            dataset.job_id = ""
            dataset.save(update_fields=["status", "job_id"])

            progress_recorder.set_progress(100, 100, "Clone complete")

            duplicates_skipped = len(evaluation_messages) - len(messages_to_add)
            return {"success": True, "created_count": len(created_messages), "duplicates_skipped": duplicates_skipped}

    except Exception as e:
        logger.exception(f"Error in session-mode clone task for dataset {dataset_id}: {e}")
        message = "An error occurred while creating session-mode messages"
        _save_dataset_error(dataset, message)
        return {"success": False, "error": message}
```

**Important:** Check if `values_list("metadata__session_id")` works with `SanitizedJSONField`. If not, the dedup query needs to use a JSON lookup. Test this at runtime. If it doesn't work, use:

```python
from django.db.models import Value
from django.db.models.functions import Cast
from django.db.models import TextField

existing_session_ids = set()
for meta in dataset.messages.values_list("metadata", flat=True):
    if meta and "session_id" in meta:
        existing_session_ids.add(meta["session_id"])
```

- [ ] **Step 2: Verify the task module imports are correct**

Check that `ProgressRecorder`, `DatasetCreationStatus`, `EvaluationDataset`, `EvaluationMessage`, `_save_dataset_error`, `current_team`, and `logger` are all already imported at the top of `tasks.py`. They should be, since the existing `create_dataset_from_sessions_task` uses them.

- [ ] **Step 3: Commit**

```bash
git add apps/evaluations/tasks.py
git commit -m "feat: add Celery task for session-mode dataset creation"
```

---

### Task 6: Add `evaluation_mode` to dataset forms

**Files:**
- Modify: `apps/evaluations/forms.py`
- Test: `apps/evaluations/tests/test_session_mode.py`

- [ ] **Step 1: Write failing test for dataset form with session mode**

Add to `apps/evaluations/tests/test_session_mode.py`:

```python
from apps.evaluations.forms import EvaluationDatasetForm, EvaluationDatasetEditForm
from apps.evaluations.models import EvaluationDataset, EvaluationMode
from apps.utils.factories.evaluations import EvaluationDatasetFactory


@pytest.mark.django_db()
class TestDatasetFormEvaluationMode:
    def test_create_form_includes_evaluation_mode_field(self):
        team = TeamFactory.create()
        form = EvaluationDatasetForm(team=team)
        assert "evaluation_mode" in form.fields

    def test_create_form_session_mode_only_allows_clone(self):
        """When mode is session, only clone is valid."""
        team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team)

        form = EvaluationDatasetForm(
            team=team,
            data={
                "name": "Test Session Dataset",
                "evaluation_mode": "session",
                "mode": "manual",
                "messages_json": '[{"human": {"content": "Hi", "role": "human"}, "ai": {"content": "Hello", "role": "ai"}, "context": {}}]',
            },
        )
        assert not form.is_valid()
        assert "mode" in form.errors or "__all__" in form.errors

    def test_edit_form_excludes_evaluation_mode_field(self):
        """evaluation_mode is immutable — excluded from edit form."""
        team = TeamFactory.create()
        dataset = EvaluationDatasetFactory.create(team=team, evaluation_mode="session")
        form = EvaluationDatasetEditForm(team=team, instance=dataset)
        assert "evaluation_mode" not in form.fields
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/evaluations/tests/test_session_mode.py::TestDatasetFormEvaluationMode -v`

Expected: FAIL — `evaluation_mode` field not in form.

- [ ] **Step 3: Add `evaluation_mode` to `EvaluationDatasetBaseForm` and `EvaluationDatasetForm`**

In `apps/evaluations/forms.py`:

First, add the import at the top (near other model imports):
```python
from apps.evaluations.models import EvaluationMode
```

In `EvaluationDatasetBaseForm.Meta` (around line 289), add `evaluation_mode` to fields:
```python
class Meta:
    model = EvaluationDataset
    fields = ("name", "evaluation_mode")
```

Add the `evaluation_mode` field to `EvaluationDatasetBaseForm` (after `MODE_CHOICES`, around line 276):
```python
evaluation_mode = forms.ChoiceField(
    choices=EvaluationMode.choices,
    initial=EvaluationMode.MESSAGE,
    widget=StyledRadioSelect(),
    label="Evaluation mode",
    help_text="Message mode evaluates individual message pairs. Session mode evaluates entire conversations.",
)
```

In `EvaluationDatasetForm.clean()` (around line 391), add validation for session mode:
```python
def clean(self):
    cleaned_data = super().clean()
    mode = cleaned_data.get("mode")
    evaluation_mode = cleaned_data.get("evaluation_mode")

    if evaluation_mode == EvaluationMode.SESSION and mode != "clone":
        raise forms.ValidationError(
            {"mode": "Session-mode datasets can only be created by cloning from sessions."}
        )

    if mode == "clone":
        session_ids, filtered_session_ids = self._clean_clone()
        cleaned_data["session_ids"] = session_ids
        cleaned_data["filtered_session_ids"] = filtered_session_ids
    elif mode == "manual":
        cleaned_data["message_pairs"] = self._clean_manual()
    elif mode == "csv":
        csv_file, column_mapping, history_column = self._clean_csv()
        cleaned_data["csv_file"] = csv_file
        cleaned_data["column_mapping"] = column_mapping
        cleaned_data["history_column"] = history_column
    return cleaned_data
```

Update `EvaluationDatasetForm.save()` to dispatch the session-mode task. In the `save` method (around line 554), update the clone branch:

```python
def save(self, commit=True):
    """Create dataset based on the selected mode."""
    dataset = super().save(commit=False)

    if not commit:
        return dataset

    dataset.status = DatasetCreationStatus.PENDING
    dataset.save()

    mode = self.cleaned_data.get("mode")

    if mode == "manual":
        self._save_manual(dataset)
        dataset.status = DatasetCreationStatus.COMPLETED
        dataset.save(update_fields=["status"])
        return dataset

    if mode == "clone":
        if dataset.evaluation_mode == EvaluationMode.SESSION:
            self._save_session_clone(dataset)
        else:
            self._save_clone(dataset)
    elif mode == "csv":
        self._save_csv(dataset)

    return dataset
```

Add the `_save_session_clone` method to `EvaluationDatasetForm`:

```python
def _save_session_clone(self, dataset):
    """Dispatch async task to create session-mode messages."""
    from apps.evaluations.tasks import create_session_mode_dataset_task  # noqa: PLC0415

    session_ids = self.cleaned_data.get("session_ids", set())
    filtered_session_ids = self.cleaned_data.get("filtered_session_ids", set())
    all_session_ids = list(session_ids | filtered_session_ids)

    if not all_session_ids:
        return

    task = create_session_mode_dataset_task.delay(
        dataset.id,
        self.team.id,
        all_session_ids,
    )

    dataset.job_id = task.id
    dataset.save(update_fields=["job_id"])
```

In `EvaluationDatasetEditForm`, exclude `evaluation_mode` from the form. Override `Meta` in `EvaluationDatasetEditForm`:

```python
class Meta(EvaluationDatasetBaseForm.Meta):
    fields = ("name",)
```

And remove `evaluation_mode` from the fields in `__init__` if it's inherited:

```python
def __init__(self, team, *args, **kwargs):
    super().__init__(team, *args, **kwargs)
    self.fields["mode"].label = "Add messages mode"
    # evaluation_mode is immutable after creation
    if "evaluation_mode" in self.fields:
        del self.fields["evaluation_mode"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/evaluations/tests/test_session_mode.py::TestDatasetFormEvaluationMode -v`

Expected: All 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/evaluations/forms.py apps/evaluations/tests/test_session_mode.py
git commit -m "feat: add evaluation_mode to dataset creation form with session-mode validation"
```

---

### Task 7: Add `evaluation_mode` to evaluator form

**Files:**
- Modify: `apps/evaluations/forms.py`

- [ ] **Step 1: Add `evaluation_mode` field to `EvaluatorForm`**

In `apps/evaluations/forms.py`, update `EvaluatorForm`:

```python
class EvaluatorForm(forms.ModelForm):
    class Meta:
        model = Evaluator
        fields = ("name", "type", "params", "evaluation_mode")
        widgets = {
            "type": forms.HiddenInput(),
            "params": forms.HiddenInput(),
        }

    def __init__(self, team, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.team = team
        self.fields["evaluation_mode"].widget = forms.RadioSelect(
            choices=EvaluationMode.choices,
        )
```

- [ ] **Step 2: Run existing evaluator tests to verify nothing broke**

Run: `uv run pytest apps/evaluations/tests/ -v --timeout=30`

Expected: All tests pass.

- [ ] **Step 3: Commit**

```bash
git add apps/evaluations/forms.py
git commit -m "feat: add evaluation_mode to evaluator form"
```

---

### Task 8: Add eval config form validation for mode matching

**Files:**
- Modify: `apps/evaluations/forms.py`
- Test: `apps/evaluations/tests/test_session_mode.py`

- [ ] **Step 1: Write failing tests for eval config form validation**

Add to `apps/evaluations/tests/test_session_mode.py`:

```python
from apps.evaluations.forms import EvaluationConfigForm
from apps.utils.factories.evaluations import EvaluationDatasetFactory, EvaluatorFactory


@pytest.mark.django_db()
class TestEvalConfigFormModeValidation:
    def test_mismatched_modes_rejected(self):
        """Dataset evaluation_mode must match all evaluator evaluation_modes."""
        team = TeamFactory.create()
        dataset = EvaluationDatasetFactory.create(team=team, evaluation_mode="session")
        evaluator = EvaluatorFactory.create(team=team, evaluation_mode="message")

        form = EvaluationConfigForm(
            team=team,
            data={
                "name": "Test Config",
                "dataset": dataset.id,
                "evaluators": [evaluator.id],
            },
        )
        assert not form.is_valid()
        assert "evaluators" in form.errors or "__all__" in form.errors

    def test_matching_modes_accepted(self):
        """Same evaluation_mode on dataset and evaluators is valid."""
        team = TeamFactory.create()
        dataset = EvaluationDatasetFactory.create(team=team, evaluation_mode="session")
        evaluator = EvaluatorFactory.create(team=team, evaluation_mode="session")

        form = EvaluationConfigForm(
            team=team,
            data={
                "name": "Test Config",
                "dataset": dataset.id,
                "evaluators": [evaluator.id],
            },
        )
        # Form should be valid (ignoring experiment-related fields which are optional when run_generation is off)
        assert form.is_valid(), form.errors

    def test_evaluators_pre_filtered_by_dataset_mode(self):
        """Evaluator queryset should be filtered by dataset's evaluation_mode."""
        team = TeamFactory.create()
        dataset = EvaluationDatasetFactory.create(team=team, evaluation_mode="session")
        msg_evaluator = EvaluatorFactory.create(team=team, evaluation_mode="message", name="Message Eval")
        sess_evaluator = EvaluatorFactory.create(team=team, evaluation_mode="session", name="Session Eval")

        form = EvaluationConfigForm(
            team=team,
            data={
                "name": "Test Config",
                "dataset": dataset.id,
                "evaluators": [sess_evaluator.id],
            },
        )
        # Note: pre-filtering happens dynamically via HTMX, but clean() validates as backend safety net
        assert form.is_valid(), form.errors
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/evaluations/tests/test_session_mode.py::TestEvalConfigFormModeValidation -v`

Expected: FAIL — no mode validation exists yet.

- [ ] **Step 3: Add `clean()` validation to `EvaluationConfigForm`**

In `apps/evaluations/forms.py`, update `EvaluationConfigForm.clean()` (around line 153). Add mode validation at the beginning of the method:

```python
def clean(self):
    cleaned_data = super().clean()

    # Validate evaluation_mode compatibility
    dataset = cleaned_data.get("dataset")
    evaluators = cleaned_data.get("evaluators")
    if dataset and evaluators:
        mismatched = [e for e in evaluators if e.evaluation_mode != dataset.evaluation_mode]
        if mismatched:
            names = ", ".join(e.name for e in mismatched)
            self.add_error(
                "evaluators",
                f"The following evaluators have a different evaluation mode than the dataset: {names}. "
                f"Dataset mode is '{dataset.evaluation_mode}', but these evaluators are not.",
            )

    experiment_version = cleaned_data.get("experiment_version")
    experiment = cleaned_data.get("experiment")

    # ... rest of existing clean() code unchanged ...
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest apps/evaluations/tests/test_session_mode.py::TestEvalConfigFormModeValidation -v`

Expected: All 3 tests PASS.

- [ ] **Step 5: Run all evaluation tests to verify nothing broke**

Run: `uv run pytest apps/evaluations/tests/ -v --timeout=60`

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add apps/evaluations/forms.py apps/evaluations/tests/test_session_mode.py
git commit -m "feat: add evaluation_mode matching validation to EvaluationConfigForm"
```

---

### Task 9: Add duplicate detection test for session-mode clone

**Files:**
- Test: `apps/evaluations/tests/test_session_mode.py`

- [ ] **Step 1: Write test for duplicate detection**

Add to `apps/evaluations/tests/test_session_mode.py`:

```python
from apps.evaluations.models import EvaluationDataset, EvaluationMessage, EvaluationMode


@pytest.mark.django_db()
class TestSessionModeDuplicateDetection:
    def test_duplicate_session_skipped(self):
        """Re-importing the same session should be skipped via metadata.session_id."""
        team = TeamFactory.create()
        session = ExperimentSessionFactory.create(team=team)
        chat = session.chat

        ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.HUMAN, content="Hello")
        ChatMessageFactory.create(chat=chat, message_type=ChatMessageType.AI, content="Hi!")

        # First import
        messages = make_session_evaluation_messages([session.external_id])
        assert len(messages) == 1

        created = EvaluationMessage.objects.bulk_create(messages)
        dataset = EvaluationDatasetFactory.create(team=team, evaluation_mode="session", messages=created)

        # Second import — same session
        messages_2 = make_session_evaluation_messages([session.external_id])
        assert len(messages_2) == 1

        # Dedup check (simulating what the task does)
        existing_session_ids = set()
        for meta in dataset.messages.values_list("metadata", flat=True):
            if meta and "session_id" in meta:
                existing_session_ids.add(meta["session_id"])

        new_messages = [
            msg for msg in messages_2
            if msg.metadata.get("session_id") not in existing_session_ids
        ]
        assert len(new_messages) == 0
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run pytest apps/evaluations/tests/test_session_mode.py::TestSessionModeDuplicateDetection -v`

Expected: PASS (this is a functional test of the dedup logic, not TDD since the logic is in the task).

- [ ] **Step 3: Commit**

```bash
git add apps/evaluations/tests/test_session_mode.py
git commit -m "test: add duplicate detection test for session-mode clone"
```

---

### Task 10: Update dataset creation template for evaluation mode

**Files:**
- Modify: `templates/evaluations/dataset_create_form.html`

- [ ] **Step 1: Add evaluation_mode field and conditional logic**

In `templates/evaluations/dataset_create_form.html`, add the evaluation_mode field before the mode selector. Update the beginning of the form block (around lines 4-9):

```html
{% block form %}
  {{ form.non_field_errors }}
  {% render_form_fields form "name" %}
  {% render_form_fields form "evaluation_mode" %}

  <div x-data="datasetModeSelectorBuilder()" x-init="init()">
    {% render_form_fields form "mode" "session_ids" "filtered_session_ids" "messages_json" "column_mapping" "csv_file_id" %}
```

Then, in the Alpine.js component `datasetModeSelectorBuilder()`, add evaluation mode awareness. Find the script block at the bottom of the template. Add a watcher for the evaluation_mode radio buttons. In the `init()` method, add:

```javascript
// Watch evaluation_mode changes to restrict available modes
const evaluationModeRadios = document.querySelectorAll('input[name="evaluation_mode"]');
evaluationModeRadios.forEach(radio => {
    radio.addEventListener('change', (e) => {
        this.evaluationMode = e.target.value;
        if (this.evaluationMode === 'session') {
            this.mode = 'clone';
        }
    });
});

// Initialize evaluationMode from the checked radio
const checkedRadio = document.querySelector('input[name="evaluation_mode"]:checked');
this.evaluationMode = checkedRadio ? checkedRadio.value : 'message';
```

Add `evaluationMode: 'message'` to the component data properties.

For the mode selector radio buttons, add conditional disabling. Wrap the manual and CSV mode options with an `x-show` or disable them when `evaluationMode === 'session'`. The simplest approach is to hide the manual and CSV mode radio items. Add after the `{% render_form_fields form "mode" ... %}` line:

```html
<template x-if="evaluationMode === 'session'">
    <div class="alert alert-info mt-2 mb-2">
        <i class="fa-solid fa-info-circle"></i>
        <span>Session-mode datasets can only be created by cloning from sessions.</span>
    </div>
</template>
```

And hide manual/CSV content sections when in session mode:

In the manual mode section (around line 49), change:
```html
<div x-show="mode === 'manual' && evaluationMode !== 'session'" class="w-full mt-4">
```

In the CSV mode section, similarly change:
```html
<div x-show="mode === 'csv' && evaluationMode !== 'session'" class="w-full mt-4">
```

- [ ] **Step 2: Verify the template renders correctly**

Run the dev server: `uv run inv runserver`

Navigate to the dataset creation page. Verify:
- evaluation_mode radio buttons appear above the mode selector
- Selecting "Session" mode forces clone mode and hides manual/CSV options
- Selecting "Message" mode shows all three options as before

- [ ] **Step 3: Commit**

```bash
git add templates/evaluations/dataset_create_form.html
git commit -m "feat: add evaluation_mode selector to dataset creation form"
```

---

### Task 11: Update evaluator form template for mode-aware prompt variables

**Files:**
- Modify: `templates/evaluations/evaluator_form.html`

- [ ] **Step 1: Add evaluation_mode field and dynamic autocomplete vars**

In `templates/evaluations/evaluator_form.html`, add the evaluation_mode form field after the name field (around line 8):

```html
{% render_form_fields form "name" %}
{% render_form_fields form "evaluation_mode" %}
```

In the text editor widget section (around line 116-132), make the `data-autocomplete-vars` dynamic based on evaluation mode. Replace the static `data-autocomplete-vars` attribute:

```html
<div class="prompt-editor textarea textarea-bordered w-full p-0 overflow-auto resize-y"
     :data-target-field="'#param_' + fieldName"
     :data-autocomplete-vars="evaluationMode === 'session'
         ? '[\"full_history\", \"context\"]'
         : '[\"input.content\", \"output.content\", \"context\", \"full_history\", \"generated_response\"]'"
     style="height: 300px; min-height: 150px; max-height: 1000px;"></div>
```

In the Alpine.js `evaluatorForm` component data, add:

```javascript
evaluationMode: '{{ form.evaluation_mode.value|default:"message" }}',
```

Add a watcher in the `init()` method to track evaluation_mode changes:

```javascript
const evalModeRadios = document.querySelectorAll('input[name="evaluation_mode"]');
evalModeRadios.forEach(radio => {
    radio.addEventListener('change', (e) => {
        this.evaluationMode = e.target.value;
        // Reinitialize prompt editors to update autocomplete vars
        this.$nextTick(() => {
            document.querySelectorAll('.prompt-editor').forEach(el => {
                if (el._codeMirrorInstance) {
                    // Update the autocomplete vars by triggering re-init
                    el.dispatchEvent(new CustomEvent('autocomplete-update'));
                }
            });
        });
    });
});
```

Also add help text that changes based on mode. After the text editor template section, add:

```html
<!-- Mode-specific variable hints -->
<template x-if="getWidget(field) === 'text_editor_widget'">
    <div class="mt-1">
        <template x-if="evaluationMode === 'session'">
            <p class="text-sm text-gray-500">
                Available variables: <code>{full_history}</code>, <code>{context.[param]}</code>
            </p>
        </template>
        <template x-if="evaluationMode !== 'session'">
            <p class="text-sm text-gray-500">
                Available variables: <code>{input.content}</code>, <code>{output.content}</code>,
                <code>{context.[param]}</code>, <code>{full_history}</code>, <code>{generated_response}</code>
            </p>
        </template>
    </div>
</template>
```

- [ ] **Step 2: Verify the template renders correctly**

Run the dev server: `uv run inv runserver`

Navigate to the evaluator creation page. Verify:
- evaluation_mode radio buttons appear
- Selecting "Session" shows only `{full_history}` and `{context.[param]}` in hints
- Selecting "Message" shows all variables

- [ ] **Step 3: Commit**

```bash
git add templates/evaluations/evaluator_form.html
git commit -m "feat: add evaluation_mode to evaluator form with dynamic prompt variable hints"
```

---

### Task 12: Update eval config form template to hide generation for session-mode

**Files:**
- Modify: `templates/evaluations/evaluation_config_form.html`

- [ ] **Step 1: Add Alpine.js logic to hide generation experiment for session-mode datasets**

In `templates/evaluations/evaluation_config_form.html`, the Alpine.js `x-data` is on line 5. Update it to track the selected dataset's evaluation_mode:

```html
<div x-data="{
    runGeneration: {{ form.run_generation.value|yesno:'true,false' }},
    datasetMode: '{{ form.dataset.value|default:"" }}' ? '{{ form.instance.dataset.evaluation_mode|default:"message" }}' : 'message',
    isSessionMode() { return this.datasetMode === 'session'; }
}" x-init="
    // Watch dataset selection changes
    const datasetSelect = document.querySelector('[name=dataset]');
    if (datasetSelect) {
        datasetSelect.addEventListener('change', async (e) => {
            const datasetId = e.target.value;
            if (!datasetId) {
                this.datasetMode = 'message';
                return;
            }
            // Fetch dataset mode via HTMX or inline data
            const option = e.target.selectedOptions[0];
            this.datasetMode = option?.dataset?.evaluationMode || 'message';
            if (this.isSessionMode()) {
                this.runGeneration = false;
            }
        });
    }
">
```

To make the dataset mode available on each option, pass it as a data attribute. This requires modifying how the dataset field is rendered. The simplest approach: render the dataset select manually instead of using `{% render_form_fields %}`.

Replace `{% render_form_fields form "dataset" %}` with a custom select:

```html
<div class="fieldset w-full">
    <label class="label font-bold" for="{{ form.dataset.id_for_label }}">
        <div>{{ form.dataset.label }}</div>
    </label>
    <select name="{{ form.dataset.name }}"
            id="{{ form.dataset.id_for_label }}"
            class="select w-full"
            @change="
                const opt = $el.selectedOptions[0];
                datasetMode = opt?.dataset?.evaluationMode || 'message';
                if (datasetMode === 'session') { runGeneration = false; }
            ">
        <option value="">---------</option>
        {% for dataset in form.dataset.field.queryset %}
            <option value="{{ dataset.pk }}"
                    data-evaluation-mode="{{ dataset.evaluation_mode }}"
                    {% if form.dataset.value|stringformat:"s" == dataset.pk|stringformat:"s" %}selected{% endif %}>
                {{ dataset }}
            </option>
        {% endfor %}
    </select>
    {{ form.dataset.errors }}
</div>
```

Then hide the `run_generation` checkbox and generation experiment section when session mode is active:

```html
<div x-show="!isSessionMode()">
    {% render_form_fields form "run_generation" %}
</div>

<template x-if="isSessionMode()">
    <div class="alert alert-info mb-4">
        <i class="fa-solid fa-info-circle"></i>
        <span>Generation is not available for session-mode datasets.</span>
    </div>
</template>
```

Update the generation experiment section (around line 13) to also check session mode:

```html
<div class="fieldset w-full" x-show="runGeneration && !isSessionMode()" x-cloak x-collapse>
```

And the version select container (around line 49):
```html
<div x-show="runGeneration && !isSessionMode()" x-cloak x-collapse>
```

- [ ] **Step 2: Verify the template renders correctly**

Run the dev server and navigate to the eval config page. Verify:
- Selecting a session-mode dataset hides the "Run generation" checkbox and experiment selection
- Selecting a message-mode dataset shows them normally
- An info alert appears when session mode is active

- [ ] **Step 3: Commit**

```bash
git add templates/evaluations/evaluation_config_form.html
git commit -m "feat: hide generation experiment section for session-mode datasets in eval config"
```

---

### Task 13: Lint, type-check, and final test run

**Files:**
- All modified files

- [ ] **Step 1: Lint all modified Python files**

```bash
uv run ruff check apps/evaluations/models.py apps/evaluations/utils.py apps/evaluations/forms.py apps/evaluations/tasks.py apps/utils/factories/evaluations.py apps/evaluations/tests/test_session_mode.py --fix
uv run ruff format apps/evaluations/models.py apps/evaluations/utils.py apps/evaluations/forms.py apps/evaluations/tasks.py apps/utils/factories/evaluations.py apps/evaluations/tests/test_session_mode.py
```

- [ ] **Step 2: Run all evaluation tests**

```bash
uv run pytest apps/evaluations/tests/ -v --timeout=60
```

Expected: All tests pass (existing + new).

- [ ] **Step 3: Run JS lint on modified templates** (if applicable)

```bash
npm run lint templates/evaluations/dataset_create_form.html templates/evaluations/evaluator_form.html templates/evaluations/evaluation_config_form.html 2>/dev/null || true
```

- [ ] **Step 4: Build frontend assets**

```bash
npm run dev
```

Expected: Build succeeds.

- [ ] **Step 5: Commit any lint fixes**

```bash
git add -A
git commit -m "chore: lint and format session-mode changes"
```
