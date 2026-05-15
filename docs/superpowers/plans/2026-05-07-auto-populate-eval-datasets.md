# Auto-populate Eval Datasets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let teams configure an `EvaluationDataset` to continuously ingest new sessions from a source bot via filter criteria, with optional auto-running of linked evaluation configs over only the new rows.

**Architecture:** Schema additions plus a new periodic Celery Beat task that brute-force scans each enabled `DatasetAutoPopulationRule` within a bounded `LOOKBACK_DAYS` window, dedupes against existing dataset rows, and (for opted-in `EvaluationConfig`s) enqueues a new `DELTA` evaluation run scoped to the appended rows. Manual filter-import and CSV-import paths are intentionally untouched.

**Tech Stack:** Django 5, Celery, django_celery_beat (`DatabaseScheduler`), pytest, FactoryBoy, ruff/ty, HTMX templates.

**Spec:** [`docs/superpowers/specs/2026-05-07-auto-populate-eval-datasets-design.md`](../specs/2026-05-07-auto-populate-eval-datasets-design.md)

---

## File Map

**New files:**
- `apps/evaluations/views/auto_population_views.py` — CRUD views for `DatasetAutoPopulationRule`.
- `apps/evaluations/notifications.py` — helper for the auto-disable notification.
- `apps/evaluations/migrations/0015_auto_populate_schema.py` — schema migration.
- `apps/evaluations/migrations/0016_register_auto_populate_periodic.py` — beat-task data migration.
- `templates/evaluations/auto_population_rule_form.html` — rule create/edit page.
- `templates/evaluations/components/auto_population_rules_panel.html` — partial included on dataset edit.
- Test files in `apps/evaluations/tests/`: `test_auto_population_models.py`, `test_auto_population_form.py`, `test_auto_population_task.py`, `test_delta_evaluation_run.py`.

**Modified files:**
- `apps/evaluations/models.py` — add `DatasetAutoPopulationRule`, `EvaluationConfig.auto_run_on_append`, `EvaluationRunType.DELTA`, `EvaluationRun.scoped_messages` M2M; update `EvaluationConfig.run`.
- `apps/evaluations/tasks.py` — add `_ingest_rule`, `_handle_rule_failure`, `auto_populate_eval_datasets`; teach `run_evaluation_task` to honour `scoped_messages`.
- `apps/evaluations/admin.py` — register the new model.
- `apps/evaluations/forms.py` — add `DatasetAutoPopulationRuleForm`; add `auto_run_on_append` checkbox to `EvaluationConfigForm`.
- `apps/evaluations/urls.py` — wire rule CRUD URLs.
- `apps/evaluations/tables.py` — add `DatasetAutoPopulationRuleTable`; teach `EvaluationRunTable` to render `type` + scope size.
- `apps/utils/factories/evaluations.py` — add `DatasetAutoPopulationRuleFactory`.
- `templates/evaluations/dataset_edit.html` — include the rules panel.
- `templates/evaluations/evaluation_config_form.html` — add the `auto_run_on_append` checkbox.
- `config/settings.py` — add `EVALUATIONS_AUTO_POPULATION_LOOKBACK_DAYS`.

---

## Conventions

- Always run targeted tests via `uv run pytest <path> -v` from the repo root.
- After each implementation step that touches Python files: `uv run ruff check <path> --fix && uv run ruff format <path>`.
- After each task that touches Python: `uv run ty check apps/evaluations` (allow pre-existing repo issues; only block on regressions in new code).
- Commit small. Each task ends with a `git commit`.
- Prefer the existing patterns: `BaseTeamModel`, `LoginAndTeamRequiredMixin`, `PermissionRequiredMixin`, `DjangoModelFactory`, `pytest.mark.django_db()`.

---

## Task 1: Add `DELTA` run type and `scoped_messages` M2M

**Files:**
- Modify: `apps/evaluations/models.py` (around `EvaluationRunType`, `EvaluationRun`)
- Create: `apps/evaluations/migrations/0015_auto_populate_schema.py` (this migration grows across Tasks 1, 2, 5 — generate after all schema fields are in place; for now just plan the field changes)
- Test: `apps/evaluations/tests/test_delta_evaluation_run.py`

- [ ] **Step 1: Write the failing test**

```python
# apps/evaluations/tests/test_delta_evaluation_run.py
import pytest

from apps.evaluations.models import EvaluationRunType
from apps.utils.factories.evaluations import (
    EvaluationMessageFactory,
    EvaluationRunFactory,
)


@pytest.mark.django_db()
def test_evaluation_run_can_be_created_as_delta_with_scope():
    run = EvaluationRunFactory.create(type=EvaluationRunType.DELTA)
    msg = EvaluationMessageFactory.create()
    run.scoped_messages.add(msg)

    run.refresh_from_db()
    assert run.type == EvaluationRunType.DELTA
    assert list(run.scoped_messages.all()) == [msg]


@pytest.mark.django_db()
def test_full_run_has_empty_scope_by_default():
    run = EvaluationRunFactory.create(type=EvaluationRunType.FULL)
    assert run.scoped_messages.count() == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest apps/evaluations/tests/test_delta_evaluation_run.py -v`
Expected: FAIL — `EvaluationRunType` has no `DELTA`, `EvaluationRun` has no `scoped_messages`.

- [ ] **Step 3: Add `DELTA` to `EvaluationRunType`**

In `apps/evaluations/models.py`, locate `class EvaluationRunType` and add the choice:

```python
class EvaluationRunType(models.TextChoices):
    FULL = "full", "Full"
    PREVIEW = "preview", "Preview"
    DELTA = "delta", "Delta"
```

- [ ] **Step 4: Add `scoped_messages` M2M to `EvaluationRun`**

In `apps/evaluations/models.py`, inside `class EvaluationRun(BaseTeamModel)`, add:

```python
    scoped_messages = models.ManyToManyField(
        EvaluationMessage,
        blank=True,
        related_name="scoping_runs",
        help_text="Subset of dataset messages this run evaluated. Empty for FULL/PREVIEW.",
    )
```

- [ ] **Step 5: Generate the migration**

Run: `uv run python manage.py makemigrations evaluations --name auto_populate_schema`
Expected: creates `apps/evaluations/migrations/0015_auto_populate_schema.py` with `AddField scoped_messages` and an `AlterField` for `EvaluationRun.type` (DELTA added).

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest apps/evaluations/tests/test_delta_evaluation_run.py -v`
Expected: PASS.

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check apps/evaluations --fix
uv run ruff format apps/evaluations
git add apps/evaluations/models.py apps/evaluations/migrations/0015_auto_populate_schema.py apps/evaluations/tests/test_delta_evaluation_run.py
git commit -m "feat(evaluations): add DELTA run type and scoped_messages M2M"
```

---

## Task 2: Add `EvaluationConfig.auto_run_on_append`

**Files:**
- Modify: `apps/evaluations/models.py` (`EvaluationConfig`)
- Modify: `apps/evaluations/migrations/0015_auto_populate_schema.py` (regenerate)
- Test: `apps/evaluations/tests/test_delta_evaluation_run.py`

- [ ] **Step 1: Add the failing test**

Append to `apps/evaluations/tests/test_delta_evaluation_run.py`:

```python
from apps.utils.factories.evaluations import EvaluationConfigFactory


@pytest.mark.django_db()
def test_evaluation_config_auto_run_on_append_defaults_false():
    config = EvaluationConfigFactory.create()
    assert config.auto_run_on_append is False


@pytest.mark.django_db()
def test_evaluation_config_auto_run_on_append_can_be_set():
    config = EvaluationConfigFactory.create(auto_run_on_append=True)
    assert config.auto_run_on_append is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest apps/evaluations/tests/test_delta_evaluation_run.py::test_evaluation_config_auto_run_on_append_defaults_false -v`
Expected: FAIL — field does not exist.

- [ ] **Step 3: Add the field**

In `apps/evaluations/models.py`, inside `class EvaluationConfig(BaseTeamModel)`, add:

```python
    auto_run_on_append = models.BooleanField(
        default=False,
        help_text=(
            "When enabled, every time the dataset receives newly auto-populated rows "
            "this evaluation runs automatically over only those rows. May incur LLM cost."
        ),
    )
```

- [ ] **Step 4: Regenerate the migration**

Delete the just-generated `0015_auto_populate_schema.py` (we will regenerate after all Task-1/2/5 schema is in place):

```bash
rm apps/evaluations/migrations/0015_auto_populate_schema.py
uv run python manage.py makemigrations evaluations --name auto_populate_schema
```

Expected: new `0015_auto_populate_schema.py` with both the DELTA / scoped_messages additions and the `auto_run_on_append` field.

- [ ] **Step 5: Run tests**

Run: `uv run pytest apps/evaluations/tests/test_delta_evaluation_run.py -v`
Expected: ALL PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/evaluations/models.py apps/evaluations/migrations/0015_auto_populate_schema.py apps/evaluations/tests/test_delta_evaluation_run.py
git commit -m "feat(evaluations): add EvaluationConfig.auto_run_on_append flag"
```

---

## Task 3: Teach `EvaluationConfig.run` to accept `scoped_messages`

**Files:**
- Modify: `apps/evaluations/models.py` (`EvaluationConfig.run`)
- Test: `apps/evaluations/tests/test_delta_evaluation_run.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
from unittest.mock import patch

from apps.evaluations.models import EvaluationRun


@pytest.mark.django_db()
def test_run_with_scoped_messages_persists_scope():
    config = EvaluationConfigFactory.create()
    msg1 = EvaluationMessageFactory.create()
    msg2 = EvaluationMessageFactory.create()

    with patch("apps.evaluations.tasks.run_evaluation_task.delay") as mock_delay:
        run = config.run(run_type=EvaluationRunType.DELTA, scoped_messages=[msg1, msg2])

    assert run.type == EvaluationRunType.DELTA
    assert set(run.scoped_messages.all()) == {msg1, msg2}
    mock_delay.assert_called_once_with(run.id)


@pytest.mark.django_db()
def test_run_without_scoped_messages_has_empty_scope():
    config = EvaluationConfigFactory.create()
    with patch("apps.evaluations.tasks.run_evaluation_task.delay"):
        run = config.run()
    assert run.type == EvaluationRunType.FULL
    assert run.scoped_messages.count() == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest apps/evaluations/tests/test_delta_evaluation_run.py::test_run_with_scoped_messages_persists_scope -v`
Expected: FAIL — `run()` does not accept `scoped_messages`.

- [ ] **Step 3: Update `EvaluationConfig.run`**

In `apps/evaluations/models.py`, replace the existing `EvaluationConfig.run` method with:

```python
    def run(
        self,
        run_type: EvaluationRunType = EvaluationRunType.FULL,
        scoped_messages: list["EvaluationMessage"] | None = None,
    ) -> "EvaluationRun":
        """Runs the evaluation asynchronously using Celery.

        When `scoped_messages` is provided, the run only evaluates those
        messages instead of the dataset's full membership.
        """
        generation_experiment = self.get_generation_experiment_version()
        run = EvaluationRun.objects.create(
            team=self.team,
            config=self,
            generation_experiment=generation_experiment,
            status=EvaluationRunStatus.PENDING,
            type=run_type,
        )
        if scoped_messages:
            run.scoped_messages.add(*scoped_messages)

        from apps.evaluations.tasks import (  # noqa: PLC0415 - circular: evaluations.tasks imports evaluations.models
            run_evaluation_task,
        )

        run_evaluation_task.delay(run.id)
        return run
```

(`run_preview` continues to call `self.run(run_type=EvaluationRunType.PREVIEW)` unchanged.)

- [ ] **Step 4: Run tests**

Run: `uv run pytest apps/evaluations/tests/test_delta_evaluation_run.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/evaluations/models.py apps/evaluations/tests/test_delta_evaluation_run.py
git commit -m "feat(evaluations): EvaluationConfig.run accepts scoped_messages"
```

---

## Task 4: Teach `run_evaluation_task` to honour `scoped_messages`

**Files:**
- Modify: `apps/evaluations/tasks.py` (`run_evaluation_task`)
- Test: `apps/evaluations/tests/test_delta_evaluation_run.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
from apps.evaluations.models import EvaluationRunStatus
from apps.evaluations.tasks import run_evaluation_task


@pytest.mark.django_db()
def test_run_evaluation_task_evaluates_only_scoped_messages_for_delta(monkeypatch):
    """Delta runs only fan out per scoped message, ignoring other dataset rows."""
    config = EvaluationConfigFactory.create()
    in_scope = EvaluationMessageFactory.create()
    out_of_scope = EvaluationMessageFactory.create()
    config.dataset.messages.add(in_scope, out_of_scope)

    run = EvaluationRun.objects.create(
        team=config.team,
        config=config,
        status=EvaluationRunStatus.PENDING,
        type=EvaluationRunType.DELTA,
    )
    run.scoped_messages.add(in_scope)

    dispatched_message_ids: list[int] = []

    def fake_chunks(chunked_args, _chunk_size):
        for evaluation_run_id, evaluator_ids, message_id in chunked_args:
            dispatched_message_ids.append(message_id)

        class _Group:
            def group(self):
                return self

        return _Group()

    class _ChordResult:
        parent = type("Parent", (), {"id": "fake", "save": lambda self: None})()

    def fake_chord(_g):
        def _runner(_callback):
            return _ChordResult()
        return _runner

    monkeypatch.setattr(
        "apps.evaluations.tasks.evaluate_single_message_task.chunks", fake_chunks
    )
    monkeypatch.setattr("apps.evaluations.tasks.chord", fake_chord)

    run_evaluation_task(run.id)

    assert dispatched_message_ids == [in_scope.id]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest apps/evaluations/tests/test_delta_evaluation_run.py::test_run_evaluation_task_evaluates_only_scoped_messages_for_delta -v`
Expected: FAIL — task dispatches both messages because it currently uses `config.dataset.messages.all()` for non-PREVIEW runs.

- [ ] **Step 3: Update `run_evaluation_task`**

In `apps/evaluations/tasks.py`, locate the message-selection block inside `run_evaluation_task` (currently `if evaluation_run.type == EvaluationRunType.PREVIEW: ... else: messages = list(message_queryset)`) and replace with:

```python
            config = evaluation_run.config
            evaluators = list(cast(Iterable[Evaluator], config.evaluators.all()))

            if evaluation_run.type == EvaluationRunType.PREVIEW:
                messages = list(config.dataset.messages.all()[:PREVIEW_SAMPLE_SIZE])
            elif evaluation_run.scoped_messages.exists():
                messages = list(evaluation_run.scoped_messages.all())
            else:
                messages = list(config.dataset.messages.all())
```

(The `prefetch_related("config__dataset__messages")` on the `EvaluationRun.objects.select_related(...)` call further up still applies; `scoped_messages.exists()` is one extra cheap query when DELTA runs are involved.)

- [ ] **Step 4: Run tests**

Run: `uv run pytest apps/evaluations/tests/test_delta_evaluation_run.py -v`
Expected: ALL PASS.

Also re-run existing evaluation-task tests to verify no regression:

Run: `uv run pytest apps/evaluations/tests/test_evaluation_tasks.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
uv run ruff check apps/evaluations --fix
uv run ruff format apps/evaluations
git add apps/evaluations/tasks.py apps/evaluations/tests/test_delta_evaluation_run.py
git commit -m "feat(evaluations): run_evaluation_task evaluates scoped_messages for delta runs"
```

---

## Task 5: Add `DatasetAutoPopulationRule` model

**Files:**
- Modify: `apps/evaluations/models.py`
- Modify: `apps/evaluations/migrations/0015_auto_populate_schema.py` (regenerate)
- Modify: `apps/utils/factories/evaluations.py` (add factory)
- Test: `apps/evaluations/tests/test_auto_population_models.py`

- [ ] **Step 1: Write the failing test**

```python
# apps/evaluations/tests/test_auto_population_models.py
import pytest
from django.core.exceptions import ValidationError

from apps.evaluations.models import DatasetAutoPopulationRule
from apps.utils.factories.evaluations import (
    DatasetAutoPopulationRuleFactory,
    EvaluationDatasetFactory,
)
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamFactory


@pytest.mark.django_db()
def test_rule_defaults():
    rule = DatasetAutoPopulationRuleFactory.create()
    assert rule.is_enabled is True
    assert rule.filter_query_string == ""
    assert rule.last_run_at is None
    assert rule.last_run_status == ""
    assert rule.last_error == ""
    assert rule.consecutive_failure_count == 0


@pytest.mark.django_db()
def test_rule_clean_rejects_dataset_team_mismatch():
    other_team = TeamFactory.create()
    dataset = EvaluationDatasetFactory.create()
    experiment = ExperimentFactory.create(team=dataset.team)
    rule = DatasetAutoPopulationRule(
        team=other_team,
        dataset=dataset,
        source_experiment=experiment,
    )
    with pytest.raises(ValidationError):
        rule.full_clean()


@pytest.mark.django_db()
def test_rule_clean_rejects_source_experiment_team_mismatch():
    dataset = EvaluationDatasetFactory.create()
    experiment = ExperimentFactory.create()  # different team
    rule = DatasetAutoPopulationRule(
        team=dataset.team,
        dataset=dataset,
        source_experiment=experiment,
    )
    with pytest.raises(ValidationError):
        rule.full_clean()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest apps/evaluations/tests/test_auto_population_models.py -v`
Expected: FAIL — `DatasetAutoPopulationRule` does not exist.

- [ ] **Step 3: Add the model**

In `apps/evaluations/models.py`, after `class EvaluationDataset` (since it FK-references it), add:

```python
class AutoPopulationRunStatus(models.TextChoices):
    SUCCESS = "success", "Success"
    ERROR = "error", "Error"
    NO_OP = "no_op", "No-op"


class DatasetAutoPopulationRule(BaseTeamModel):
    """A continuous-ingestion rule that pulls new sessions from a source experiment
    into an evaluation dataset on each polling tick."""

    AUTO_DISABLE_FAILURE_THRESHOLD = 3

    dataset = models.ForeignKey(
        EvaluationDataset,
        on_delete=models.CASCADE,
        related_name="auto_population_rules",
    )
    source_experiment = models.ForeignKey(
        "experiments.Experiment",
        on_delete=models.CASCADE,
        related_name="auto_population_rules",
        help_text="Sessions from this chatbot are considered for auto-population.",
    )
    filter_query_string = models.TextField(
        blank=True,
        help_text=(
            "Filter criteria as a query string; empty means 'all sessions from this bot'. "
            "Format matches FilterParams used elsewhere."
        ),
    )
    is_enabled = models.BooleanField(default=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_run_status = models.CharField(
        max_length=10, choices=AutoPopulationRunStatus.choices, blank=True
    )
    last_error = models.TextField(blank=True)
    consecutive_failure_count = models.PositiveSmallIntegerField(default=0)

    class Meta:
        indexes = [models.Index(fields=["is_enabled", "last_run_at"])]

    def __str__(self) -> str:
        return f"AutoPopRule({self.source_experiment_id} -> dataset {self.dataset_id})"

    def clean(self):
        super().clean()
        if self.team_id and self.dataset_id and self.dataset.team_id != self.team_id:
            raise ValidationError({"dataset": "Dataset must belong to the same team as the rule."})
        if (
            self.team_id
            and self.source_experiment_id
            and self.source_experiment.team_id != self.team_id
        ):
            raise ValidationError(
                {"source_experiment": "Source chatbot must belong to the same team as the rule."}
            )

    def get_absolute_url(self):
        return reverse(
            "evaluations:auto_population_rule_edit",
            args=[get_slug_for_team(self.team_id), self.id],
        )
```

- [ ] **Step 4: Add the factory**

In `apps/utils/factories/evaluations.py`, add (importing what is needed):

```python
from apps.evaluations.models import DatasetAutoPopulationRule
from apps.utils.factories.experiment import ExperimentFactory


class DatasetAutoPopulationRuleFactory(DjangoModelFactory):
    class Meta:
        model = DatasetAutoPopulationRule

    team = factory.SubFactory(TeamFactory)
    dataset = factory.SubFactory(EvaluationDatasetFactory, team=factory.SelfAttribute("..team"))
    source_experiment = factory.SubFactory(ExperimentFactory, team=factory.SelfAttribute("..team"))
```

- [ ] **Step 5: Regenerate the migration**

```bash
rm apps/evaluations/migrations/0015_auto_populate_schema.py
uv run python manage.py makemigrations evaluations --name auto_populate_schema
```

Expected: now contains `CreateModel DatasetAutoPopulationRule` plus the earlier additions.

- [ ] **Step 6: Run tests**

Run: `uv run pytest apps/evaluations/tests/test_auto_population_models.py apps/evaluations/tests/test_delta_evaluation_run.py -v`
Expected: ALL PASS.

- [ ] **Step 7: Commit**

```bash
uv run ruff check apps/evaluations apps/utils/factories --fix
uv run ruff format apps/evaluations apps/utils/factories
git add apps/evaluations/models.py apps/evaluations/migrations/0015_auto_populate_schema.py apps/utils/factories/evaluations.py apps/evaluations/tests/test_auto_population_models.py
git commit -m "feat(evaluations): add DatasetAutoPopulationRule model"
```

---

## Task 6: Register the model in Django admin

**Files:**
- Modify: `apps/evaluations/admin.py`

- [ ] **Step 1: Add the admin registration**

Append to `apps/evaluations/admin.py`:

```python
from apps.evaluations.models import DatasetAutoPopulationRule


@admin.register(DatasetAutoPopulationRule)
class DatasetAutoPopulationRuleAdmin(ReadonlyAdminMixin, admin.ModelAdmin):
    list_display = (
        "id",
        "team",
        "dataset",
        "source_experiment",
        "is_enabled",
        "last_run_at",
        "last_run_status",
        "consecutive_failure_count",
    )
    list_filter = ("is_enabled", "last_run_status", "team")
    search_fields = ("dataset__name", "source_experiment__name")
```

(Reuses existing `ReadonlyAdminMixin` already imported into this module.)

- [ ] **Step 2: Smoke-check**

Run: `uv run python manage.py check`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
uv run ruff check apps/evaluations/admin.py --fix
uv run ruff format apps/evaluations/admin.py
git add apps/evaluations/admin.py
git commit -m "feat(evaluations): register DatasetAutoPopulationRule admin"
```

---

## Task 7: Add lookback setting and notification helper

**Files:**
- Modify: `config/settings.py`
- Create: `apps/evaluations/notifications.py`
- Test: `apps/evaluations/tests/test_auto_population_models.py`

- [ ] **Step 1: Add the setting**

Append to `config/settings.py` (in the application-settings region near other domain constants):

```python
# How far back the auto-populate-eval-datasets task scans for new sessions per rule.
EVALUATIONS_AUTO_POPULATION_LOOKBACK_DAYS = env.int("EVALUATIONS_AUTO_POPULATION_LOOKBACK_DAYS", default=30)
```

(Use whichever helper the file already uses for env-derived ints; if there's no `env`, just use `30`. Match the surrounding style.)

- [ ] **Step 2: Write the failing test for the notification helper**

Append to `apps/evaluations/tests/test_auto_population_models.py`:

```python
from apps.evaluations.notifications import auto_population_rule_disabled_notification
from apps.ocs_notifications.models import NotificationEvent


@pytest.mark.django_db()
def test_auto_disable_notification_creates_event():
    rule = DatasetAutoPopulationRuleFactory.create()
    auto_population_rule_disabled_notification(rule, reason="three consecutive failures")

    events = NotificationEvent.objects.filter(team=rule.team)
    assert events.count() == 1
    event = events.first()
    assert "auto-population" in event.title.lower()
    assert str(rule.dataset.name) in event.message
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest apps/evaluations/tests/test_auto_population_models.py::test_auto_disable_notification_creates_event -v`
Expected: FAIL — module does not exist.

- [ ] **Step 4: Implement the notification helper**

Create `apps/evaluations/notifications.py`:

```python
import logging

from apps.evaluations.models import DatasetAutoPopulationRule
from apps.ocs_notifications.models import LevelChoices
from apps.ocs_notifications.utils import create_notification
from apps.utils.decorators import silence_exceptions

logger = logging.getLogger("ocs.evaluations")


@silence_exceptions(logger, log_message="Failed to create auto-population disable notification")
def auto_population_rule_disabled_notification(
    rule: DatasetAutoPopulationRule, reason: str
) -> None:
    """Notify team admins that an auto-population rule has been disabled."""
    create_notification(
        title="Auto-population rule disabled",
        message=(
            f"The auto-population rule for dataset '{rule.dataset.name}' "
            f"(source: {rule.source_experiment.name}) was automatically disabled: {reason}."
        ),
        level=LevelChoices.WARNING,
        team=rule.team,
        slug="evaluations-auto-population-disabled",
        event_data={"rule_id": rule.id, "dataset_id": rule.dataset_id},
        permissions=["evaluations.change_evaluationdataset"],
        links={"View dataset": rule.dataset.get_absolute_url()},
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest apps/evaluations/tests/test_auto_population_models.py::test_auto_disable_notification_creates_event -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
uv run ruff check apps/evaluations config/settings.py --fix
uv run ruff format apps/evaluations config/settings.py
git add config/settings.py apps/evaluations/notifications.py apps/evaluations/tests/test_auto_population_models.py
git commit -m "feat(evaluations): add lookback setting and auto-disable notification"
```

---

## Task 8: Implement `_ingest_rule` (session-mode happy path)

**Files:**
- Modify: `apps/evaluations/tasks.py`
- Test: `apps/evaluations/tests/test_auto_population_task.py`

- [ ] **Step 1: Write the failing test**

```python
# apps/evaluations/tests/test_auto_population_task.py
import pytest
from django.utils import timezone

from apps.evaluations.models import (
    AutoPopulationRunStatus,
    EvaluationMode,
)
from apps.evaluations.tasks import _ingest_rule
from apps.utils.factories.evaluations import (
    DatasetAutoPopulationRuleFactory,
    EvaluationDatasetFactory,
)
from apps.utils.factories.experiment import ExperimentSessionFactory


@pytest.mark.django_db()
def test_ingest_rule_session_mode_appends_new_sessions():
    dataset = EvaluationDatasetFactory.create(
        evaluation_mode=EvaluationMode.SESSION, messages=[]
    )
    rule = DatasetAutoPopulationRuleFactory.create(dataset=dataset)
    # Two sessions on this rule's source experiment with at least one chat message each.
    s1 = ExperimentSessionFactory.create(experiment=rule.source_experiment, team=rule.team)
    s2 = ExperimentSessionFactory.create(experiment=rule.source_experiment, team=rule.team)
    s1.chat.messages.create(message_type="human", content="hi from s1")
    s1.chat.messages.create(message_type="ai", content="hello from s1")
    s2.chat.messages.create(message_type="human", content="hi from s2")
    s2.chat.messages.create(message_type="ai", content="hello from s2")

    appended = _ingest_rule(rule)

    rule.refresh_from_db()
    assert len(appended) == 2
    assert dataset.messages.count() == 2
    assert {m.session_id for m in dataset.messages.all()} == {s1.id, s2.id}
    assert rule.last_run_status == AutoPopulationRunStatus.SUCCESS
    assert rule.last_run_at is not None
    assert rule.consecutive_failure_count == 0


@pytest.mark.django_db()
def test_ingest_rule_no_op_when_no_matches():
    dataset = EvaluationDatasetFactory.create(
        evaluation_mode=EvaluationMode.SESSION, messages=[]
    )
    rule = DatasetAutoPopulationRuleFactory.create(dataset=dataset)

    appended = _ingest_rule(rule)

    rule.refresh_from_db()
    assert appended == []
    assert dataset.messages.count() == 0
    assert rule.last_run_status == AutoPopulationRunStatus.NO_OP
    assert rule.last_run_at is not None
```

(`ExperimentSessionFactory` already exists at `apps/utils/factories/experiment.py`; verify its name and import path before using.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest apps/evaluations/tests/test_auto_population_task.py -v`
Expected: FAIL — `_ingest_rule` not defined.

- [ ] **Step 3: Implement `_ingest_rule` (session mode only for now)**

In `apps/evaluations/tasks.py`, add:

```python
from datetime import timedelta as _timedelta  # if not already imported

from django.conf import settings
from django.http import QueryDict

from apps.evaluations.models import (  # extend the existing import block
    AutoPopulationRunStatus,
    DatasetAutoPopulationRule,
    EvaluationMode,
)
from apps.experiments.filters import ChatMessageFilter, ExperimentSessionFilter
from apps.experiments.models import ExperimentSession
from apps.web.dynamic_filters.datastructures import FilterParams


def _ingest_rule(rule: DatasetAutoPopulationRule) -> list[EvaluationMessage]:
    """Scan the rule's source experiment for new sessions, append matches to the dataset.

    Returns the list of newly appended `EvaluationMessage` rows.
    """
    dataset = rule.dataset
    lookback_floor = timezone.now() - timedelta(
        days=settings.EVALUATIONS_AUTO_POPULATION_LOOKBACK_DAYS
    )
    created_floor = max(rule.created_at, lookback_floor)

    if dataset.evaluation_mode == EvaluationMode.SESSION:
        appended = _ingest_rule_session_mode(rule, created_floor)
    else:
        appended = _ingest_rule_message_mode(rule, created_floor)

    rule.last_run_at = timezone.now()
    if appended:
        rule.last_run_status = AutoPopulationRunStatus.SUCCESS
        rule.consecutive_failure_count = 0
        rule.last_error = ""
    else:
        rule.last_run_status = AutoPopulationRunStatus.NO_OP
    rule.save(
        update_fields=[
            "last_run_at",
            "last_run_status",
            "consecutive_failure_count",
            "last_error",
        ]
    )
    return appended


def _ingest_rule_session_mode(
    rule: DatasetAutoPopulationRule, created_floor
) -> list[EvaluationMessage]:
    qs = (
        ExperimentSession.objects.filter(
            team=rule.team,
            experiment__in=_versions_of(rule.source_experiment),
            created_at__gt=created_floor,
        )
        .exclude(
            id__in=rule.dataset.messages.filter(session__isnull=False).values_list(
                "session_id", flat=True
            )
        )
    )

    if rule.filter_query_string:
        params = FilterParams(QueryDict(rule.filter_query_string))
        qs = ExperimentSessionFilter().apply(qs, params, timezone=None)

    session_external_ids = list(qs.values_list("external_id", flat=True))
    if not session_external_ids:
        return []

    from apps.evaluations.utils import (  # noqa: PLC0415
        make_session_evaluation_messages,
    )

    eval_messages = make_session_evaluation_messages(
        session_external_ids, team=rule.team
    )
    if not eval_messages:
        return []
    created = EvaluationMessage.objects.bulk_create(eval_messages)
    rule.dataset.messages.add(*created)
    return list(created)


def _ingest_rule_message_mode(
    rule: DatasetAutoPopulationRule, created_floor
) -> list[EvaluationMessage]:
    # Implemented in Task 9.
    return []


def _versions_of(experiment):
    """Return a queryset including this experiment and all of its versions."""
    base_id = experiment.working_version_id or experiment.id
    return type(experiment).objects.filter(
        models.Q(id=base_id) | models.Q(working_version_id=base_id)
    )
```

(If `Q` is not already imported, add `from django.db.models import Q` and use `Q(...) | Q(...)`. Confirm by reading the existing imports in `apps/evaluations/tasks.py`.)

- [ ] **Step 4: Run tests**

Run: `uv run pytest apps/evaluations/tests/test_auto_population_task.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
uv run ruff check apps/evaluations --fix
uv run ruff format apps/evaluations
git add apps/evaluations/tasks.py apps/evaluations/tests/test_auto_population_task.py
git commit -m "feat(evaluations): _ingest_rule session-mode happy path"
```

---

## Task 9: Extend `_ingest_rule` — message mode, dedup, lookback, forward floor

**Files:**
- Modify: `apps/evaluations/tasks.py`
- Test: `apps/evaluations/tests/test_auto_population_task.py`

- [ ] **Step 1: Write failing tests**

Append:

```python
from datetime import timedelta as _td

from django.utils import timezone


@pytest.mark.django_db()
def test_ingest_rule_skips_sessions_already_in_dataset():
    dataset = EvaluationDatasetFactory.create(
        evaluation_mode=EvaluationMode.SESSION, messages=[]
    )
    rule = DatasetAutoPopulationRuleFactory.create(dataset=dataset)
    s1 = ExperimentSessionFactory.create(experiment=rule.source_experiment, team=rule.team)
    s1.chat.messages.create(message_type="human", content="x")
    s1.chat.messages.create(message_type="ai", content="y")

    # First tick: ingests s1.
    _ingest_rule(rule)
    assert dataset.messages.count() == 1

    # Second tick: no new sessions, dedup keeps it at 1.
    _ingest_rule(rule)
    assert dataset.messages.count() == 1


@pytest.mark.django_db()
def test_ingest_rule_skips_sessions_older_than_rule_created_at():
    dataset = EvaluationDatasetFactory.create(
        evaluation_mode=EvaluationMode.SESSION, messages=[]
    )
    rule = DatasetAutoPopulationRuleFactory.create(dataset=dataset)
    older = ExperimentSessionFactory.create(experiment=rule.source_experiment, team=rule.team)
    older.created_at = rule.created_at - _td(hours=1)
    older.save(update_fields=["created_at"])

    _ingest_rule(rule)

    assert dataset.messages.count() == 0


@pytest.mark.django_db()
def test_ingest_rule_message_mode_appends_message_pairs():
    dataset = EvaluationDatasetFactory.create(
        evaluation_mode=EvaluationMode.MESSAGE, messages=[]
    )
    rule = DatasetAutoPopulationRuleFactory.create(dataset=dataset)
    session = ExperimentSessionFactory.create(experiment=rule.source_experiment, team=rule.team)
    session.chat.messages.create(message_type="human", content="hi")
    session.chat.messages.create(message_type="ai", content="hello")

    _ingest_rule(rule)

    assert dataset.messages.count() == 1
    msg = dataset.messages.first()
    assert msg.input_chat_message_id is not None
    assert msg.expected_output_chat_message_id is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/evaluations/tests/test_auto_population_task.py -v`
Expected: the new tests FAIL (message-mode test fails outright; dedup may pass already; forward-floor will fail because `created_at` filter uses `created_floor` set to `max(rule.created_at, lookback)` so should pass — re-check).

- [ ] **Step 3: Implement message-mode ingestion and tighten dedup**

Replace the `_ingest_rule_message_mode` stub in `apps/evaluations/tasks.py` with:

```python
def _ingest_rule_message_mode(
    rule: DatasetAutoPopulationRule, created_floor
) -> list[EvaluationMessage]:
    base_qs = (
        ExperimentSession.objects.filter(
            team=rule.team,
            experiment__in=_versions_of(rule.source_experiment),
            created_at__gt=created_floor,
        )
    )
    session_external_ids = list(base_qs.values_list("external_id", flat=True))
    if not session_external_ids:
        return []

    # `EvaluationMessage.create_from_sessions` has two independent branches:
    # `external_session_ids` (no filter) and `filtered_session_ids` + `filter_params`
    # (with filter). Pick the right one based on whether the rule has a filter.
    if rule.filter_query_string:
        eval_messages = EvaluationMessage.create_from_sessions(
            team=rule.team,
            external_session_ids=None,
            filtered_session_ids=session_external_ids,
            filter_params=FilterParams(QueryDict(rule.filter_query_string)),
            timezone=None,
        )
    else:
        eval_messages = EvaluationMessage.create_from_sessions(
            team=rule.team,
            external_session_ids=session_external_ids,
        )
    if not eval_messages:
        return []

    existing_pairs = set(
        rule.dataset.messages.filter(
            input_chat_message_id__isnull=False,
            expected_output_chat_message_id__isnull=False,
        ).values_list("input_chat_message_id", "expected_output_chat_message_id")
    )
    fresh = [
        m
        for m in eval_messages
        if (m.input_chat_message_id, m.expected_output_chat_message_id)
        not in existing_pairs
    ]
    if not fresh:
        return []
    created = EvaluationMessage.objects.bulk_create(fresh)
    rule.dataset.messages.add(*created)
    return list(created)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest apps/evaluations/tests/test_auto_population_task.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
uv run ruff check apps/evaluations --fix
uv run ruff format apps/evaluations
git add apps/evaluations/tasks.py apps/evaluations/tests/test_auto_population_task.py
git commit -m "feat(evaluations): _ingest_rule supports message mode + dedup + forward floor"
```

---

## Task 10: Auto-trigger DELTA runs after successful append

**Files:**
- Modify: `apps/evaluations/tasks.py` (`_ingest_rule`)
- Test: `apps/evaluations/tests/test_auto_population_task.py`

- [ ] **Step 1: Write failing test**

Append:

```python
from unittest.mock import patch

from apps.evaluations.models import EvaluationRunType
from apps.utils.factories.evaluations import EvaluationConfigFactory


@pytest.mark.django_db()
def test_ingest_rule_triggers_delta_runs_only_for_opted_in_configs():
    dataset = EvaluationDatasetFactory.create(
        evaluation_mode=EvaluationMode.SESSION, messages=[]
    )
    rule = DatasetAutoPopulationRuleFactory.create(dataset=dataset)
    opted_in = EvaluationConfigFactory.create(
        team=dataset.team, dataset=dataset, auto_run_on_append=True
    )
    opted_out = EvaluationConfigFactory.create(
        team=dataset.team, dataset=dataset, auto_run_on_append=False
    )
    session = ExperimentSessionFactory.create(
        experiment=rule.source_experiment, team=rule.team
    )
    session.chat.messages.create(message_type="human", content="x")
    session.chat.messages.create(message_type="ai", content="y")

    with patch("apps.evaluations.tasks.run_evaluation_task.delay"):
        _ingest_rule(rule)

    runs_for_opted_in = opted_in.evaluationrun_set.filter(type=EvaluationRunType.DELTA)
    runs_for_opted_out = opted_out.evaluationrun_set.filter(type=EvaluationRunType.DELTA)
    assert runs_for_opted_in.count() == 1
    assert runs_for_opted_out.count() == 0

    delta_run = runs_for_opted_in.first()
    assert delta_run.scoped_messages.count() == 1


@pytest.mark.django_db()
def test_ingest_rule_no_appends_no_runs():
    dataset = EvaluationDatasetFactory.create(
        evaluation_mode=EvaluationMode.SESSION, messages=[]
    )
    rule = DatasetAutoPopulationRuleFactory.create(dataset=dataset)
    EvaluationConfigFactory.create(
        team=dataset.team, dataset=dataset, auto_run_on_append=True
    )

    with patch("apps.evaluations.tasks.run_evaluation_task.delay") as mock_delay:
        _ingest_rule(rule)

    mock_delay.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/evaluations/tests/test_auto_population_task.py::test_ingest_rule_triggers_delta_runs_only_for_opted_in_configs -v`
Expected: FAIL — no auto-trigger yet.

- [ ] **Step 3: Add the trigger inside `_ingest_rule`**

In `apps/evaluations/tasks.py`, modify `_ingest_rule` to dispatch DELTA runs after a successful append:

```python
def _ingest_rule(rule: DatasetAutoPopulationRule) -> list[EvaluationMessage]:
    dataset = rule.dataset
    lookback_floor = timezone.now() - timedelta(
        days=settings.EVALUATIONS_AUTO_POPULATION_LOOKBACK_DAYS
    )
    created_floor = max(rule.created_at, lookback_floor)

    if dataset.evaluation_mode == EvaluationMode.SESSION:
        appended = _ingest_rule_session_mode(rule, created_floor)
    else:
        appended = _ingest_rule_message_mode(rule, created_floor)

    rule.last_run_at = timezone.now()
    if appended:
        rule.last_run_status = AutoPopulationRunStatus.SUCCESS
        rule.consecutive_failure_count = 0
        rule.last_error = ""
    else:
        rule.last_run_status = AutoPopulationRunStatus.NO_OP
    rule.save(
        update_fields=[
            "last_run_at",
            "last_run_status",
            "consecutive_failure_count",
            "last_error",
        ]
    )

    if appended:
        _trigger_delta_runs_for_dataset(dataset, appended)
    return appended


def _trigger_delta_runs_for_dataset(
    dataset: EvaluationDataset, appended: list[EvaluationMessage]
) -> None:
    """Enqueue a DELTA evaluation run for each opted-in config on this dataset."""
    from apps.evaluations.models import (  # noqa: PLC0415
        EvaluationConfig,
        EvaluationRunType,
    )

    configs = EvaluationConfig.objects.filter(dataset=dataset, auto_run_on_append=True)
    for config in configs:
        config.run(run_type=EvaluationRunType.DELTA, scoped_messages=appended)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest apps/evaluations/tests/test_auto_population_task.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
uv run ruff check apps/evaluations --fix
uv run ruff format apps/evaluations
git add apps/evaluations/tasks.py apps/evaluations/tests/test_auto_population_task.py
git commit -m "feat(evaluations): _ingest_rule triggers delta runs on opted-in configs"
```

---

## Task 11: Failure handling and auto-disable

**Files:**
- Modify: `apps/evaluations/tasks.py`
- Test: `apps/evaluations/tests/test_auto_population_task.py`

- [ ] **Step 1: Write failing tests**

Append:

```python
from apps.ocs_notifications.models import NotificationEvent


@pytest.mark.django_db()
def test_handle_rule_failure_increments_counter_and_records_error():
    rule = DatasetAutoPopulationRuleFactory.create()
    from apps.evaluations.tasks import _handle_rule_failure

    _handle_rule_failure(rule, RuntimeError("boom"))

    rule.refresh_from_db()
    assert rule.consecutive_failure_count == 1
    assert rule.last_run_status == AutoPopulationRunStatus.ERROR
    assert "boom" in rule.last_error
    assert rule.is_enabled is True


@pytest.mark.django_db()
def test_third_consecutive_failure_disables_rule_and_emits_notification():
    rule = DatasetAutoPopulationRuleFactory.create(consecutive_failure_count=2)
    from apps.evaluations.tasks import _handle_rule_failure

    _handle_rule_failure(rule, RuntimeError("third strike"))

    rule.refresh_from_db()
    assert rule.consecutive_failure_count == 3
    assert rule.is_enabled is False
    assert NotificationEvent.objects.filter(team=rule.team).count() == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest apps/evaluations/tests/test_auto_population_task.py::test_handle_rule_failure_increments_counter_and_records_error -v`
Expected: FAIL — `_handle_rule_failure` doesn't exist.

- [ ] **Step 3: Implement `_handle_rule_failure`**

In `apps/evaluations/tasks.py`, add:

```python
from apps.evaluations.notifications import (  # noqa: PLC0415 acceptable, top-level fine here
    auto_population_rule_disabled_notification,
)


def _handle_rule_failure(
    rule: DatasetAutoPopulationRule, exception: Exception
) -> None:
    """Record a failure on the rule; auto-disable after the configured threshold."""
    rule.consecutive_failure_count = (rule.consecutive_failure_count or 0) + 1
    rule.last_run_status = AutoPopulationRunStatus.ERROR
    rule.last_run_at = timezone.now()
    rule.last_error = str(exception)[:1000]

    update_fields = [
        "consecutive_failure_count",
        "last_run_status",
        "last_run_at",
        "last_error",
    ]
    if rule.consecutive_failure_count >= DatasetAutoPopulationRule.AUTO_DISABLE_FAILURE_THRESHOLD:
        rule.is_enabled = False
        update_fields.append("is_enabled")

    rule.save(update_fields=update_fields)

    if not rule.is_enabled:
        auto_population_rule_disabled_notification(
            rule, reason=f"{rule.consecutive_failure_count} consecutive failures"
        )
```

(Move the import to the top of the module instead of inside the function — `apps/evaluations/notifications.py` does not import `apps/evaluations/tasks.py`, so there is no circularity.)

- [ ] **Step 4: Run tests**

Run: `uv run pytest apps/evaluations/tests/test_auto_population_task.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
uv run ruff check apps/evaluations --fix
uv run ruff format apps/evaluations
git add apps/evaluations/tasks.py apps/evaluations/tests/test_auto_population_task.py
git commit -m "feat(evaluations): rule failure handling and auto-disable at 3 strikes"
```

---

## Task 12: Top-level `auto_populate_eval_datasets` periodic task

**Files:**
- Modify: `apps/evaluations/tasks.py`
- Test: `apps/evaluations/tests/test_auto_population_task.py`

- [ ] **Step 1: Write failing test**

Append:

```python
@pytest.mark.django_db()
def test_auto_populate_task_skips_disabled_rules_and_isolates_failures(monkeypatch):
    from apps.evaluations import tasks

    enabled_a = DatasetAutoPopulationRuleFactory.create(is_enabled=True)
    enabled_b = DatasetAutoPopulationRuleFactory.create(is_enabled=True)
    disabled = DatasetAutoPopulationRuleFactory.create(is_enabled=False)

    processed: list[int] = []

    def fake_ingest(rule):
        processed.append(rule.id)
        if rule.id == enabled_a.id:
            raise RuntimeError("rule A blew up")
        return []

    monkeypatch.setattr(tasks, "_ingest_rule", fake_ingest)

    tasks.auto_populate_eval_datasets()

    assert disabled.id not in processed
    assert enabled_a.id in processed
    assert enabled_b.id in processed
    enabled_a.refresh_from_db()
    enabled_b.refresh_from_db()
    assert enabled_a.last_run_status == AutoPopulationRunStatus.ERROR
    assert enabled_a.consecutive_failure_count == 1
    # b should still be processed; since fake_ingest returned [], it's a no-op:
    assert enabled_b.last_run_status == AutoPopulationRunStatus.NO_OP
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest apps/evaluations/tests/test_auto_population_task.py::test_auto_populate_task_skips_disabled_rules_and_isolates_failures -v`
Expected: FAIL — `auto_populate_eval_datasets` doesn't exist.

- [ ] **Step 3: Implement the periodic task**

In `apps/evaluations/tasks.py`, add:

```python
@shared_task(base=TaskbadgerTask)
def auto_populate_eval_datasets():
    """Periodic task: walk enabled DatasetAutoPopulationRules and ingest matches.

    Each rule is processed inside its own transaction with a row-level lock
    (`select_for_update(skip_locked=True)`) so two beat workers cannot
    double-process the same rule. A failure on one rule never blocks others.
    """
    rule_ids = list(
        DatasetAutoPopulationRule.objects.filter(is_enabled=True)
        .order_by(models.F("last_run_at").asc(nulls_first=True))
        .values_list("id", flat=True)
    )
    for rule_id in rule_ids:
        try:
            with transaction.atomic():
                rule = (
                    DatasetAutoPopulationRule.objects.select_for_update(skip_locked=True)
                    .filter(id=rule_id, is_enabled=True)
                    .first()
                )
                if rule is None:
                    continue  # locked by another worker, or disabled mid-tick
                try:
                    _ingest_rule(rule)
                except Exception as e:  # noqa: BLE001 - per-rule isolation is the point
                    logger.exception("Auto-population rule %s failed: %s", rule.id, e)
                    _handle_rule_failure(rule, e)
        except Exception:  # noqa: BLE001 - last-resort guard
            logger.exception(
                "Unexpected outer error processing auto-population rule %s", rule_id
            )
```

(`models.F` and `transaction` are already imported at the top of `tasks.py`.)

- [ ] **Step 4: Run tests**

Run: `uv run pytest apps/evaluations/tests/test_auto_population_task.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
uv run ruff check apps/evaluations --fix
uv run ruff format apps/evaluations
git add apps/evaluations/tasks.py apps/evaluations/tests/test_auto_population_task.py
git commit -m "feat(evaluations): periodic task auto_populate_eval_datasets"
```

---

## Task 13: Register the periodic task with `django_celery_beat`

**Files:**
- Create: `apps/evaluations/migrations/0016_register_auto_populate_periodic.py`

- [ ] **Step 1: Hand-write the data migration**

Create `apps/evaluations/migrations/0016_register_auto_populate_periodic.py`:

```python
from django.db import migrations

TASK_NAME = "apps.evaluations.tasks.auto_populate_eval_datasets"
SCHEDULE_MINUTES = 5


def register(apps, schema_editor):
    IntervalSchedule = apps.get_model("django_celery_beat", "IntervalSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    schedule, _ = IntervalSchedule.objects.get_or_create(
        every=SCHEDULE_MINUTES,
        period="minutes",
    )
    PeriodicTask.objects.update_or_create(
        name=TASK_NAME,
        defaults={
            "interval": schedule,
            "task": TASK_NAME,
            "expire_seconds": SCHEDULE_MINUTES * 60,
        },
    )


def unregister(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(name=TASK_NAME).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("django_celery_beat", "0019_alter_periodictasks_options"),
        ("evaluations", "0015_auto_populate_schema"),
    ]

    operations = [
        migrations.RunPython(register, unregister),
    ]
```

- [ ] **Step 2: Run a dry-apply against a test database**

Run: `uv run python manage.py migrate evaluations --plan | tail -20`
Expected: shows `0016_register_auto_populate_periodic` queued behind `0015_auto_populate_schema`.

- [ ] **Step 3: Commit**

```bash
git add apps/evaluations/migrations/0016_register_auto_populate_periodic.py
git commit -m "chore(evaluations): register auto_populate_eval_datasets beat schedule"
```

---

## Task 14: `DatasetAutoPopulationRuleForm`

**Files:**
- Modify: `apps/evaluations/forms.py`
- Test: `apps/evaluations/tests/test_auto_population_form.py`

- [ ] **Step 1: Write failing tests**

```python
# apps/evaluations/tests/test_auto_population_form.py
import pytest

from apps.evaluations.forms import DatasetAutoPopulationRuleForm
from apps.utils.factories.evaluations import EvaluationDatasetFactory
from apps.utils.factories.experiment import ExperimentFactory


@pytest.mark.django_db()
def test_form_rejects_cross_team_source_experiment():
    dataset = EvaluationDatasetFactory.create()
    foreign_experiment = ExperimentFactory.create()  # different team

    form = DatasetAutoPopulationRuleForm(
        team=dataset.team,
        dataset=dataset,
        data={
            "source_experiment": foreign_experiment.id,
            "filter_query_string": "",
            "is_enabled": True,
        },
    )
    assert not form.is_valid()
    assert "source_experiment" in form.errors


@pytest.mark.django_db()
def test_form_rejects_invalid_filter_query():
    dataset = EvaluationDatasetFactory.create()
    experiment = ExperimentFactory.create(team=dataset.team)

    form = DatasetAutoPopulationRuleForm(
        team=dataset.team,
        dataset=dataset,
        data={
            "source_experiment": experiment.id,
            # Half-formed query (missing operator/value pairs):
            "filter_query_string": "filter_0_column=tags",
            "is_enabled": True,
        },
    )
    assert not form.is_valid()
    assert "filter_query_string" in form.errors


@pytest.mark.django_db()
def test_form_accepts_valid_input():
    dataset = EvaluationDatasetFactory.create()
    experiment = ExperimentFactory.create(team=dataset.team)

    form = DatasetAutoPopulationRuleForm(
        team=dataset.team,
        dataset=dataset,
        data={
            "source_experiment": experiment.id,
            "filter_query_string": "",
            "is_enabled": True,
        },
    )
    assert form.is_valid(), form.errors
    rule = form.save()
    assert rule.team == dataset.team
    assert rule.dataset == dataset
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest apps/evaluations/tests/test_auto_population_form.py -v`
Expected: FAIL — form does not exist.

- [ ] **Step 3: Implement the form**

In `apps/evaluations/forms.py`, append:

```python
from django.http import QueryDict

from apps.evaluations.models import DatasetAutoPopulationRule
from apps.experiments.models import Experiment
from apps.web.dynamic_filters.datastructures import FilterParams


class DatasetAutoPopulationRuleForm(forms.ModelForm):
    class Meta:
        model = DatasetAutoPopulationRule
        fields = ["source_experiment", "filter_query_string", "is_enabled"]

    def __init__(self, *args, team, dataset, **kwargs):
        super().__init__(*args, **kwargs)
        self.team = team
        self.dataset = dataset
        self.fields["source_experiment"].queryset = (
            Experiment.objects.working_versions_queryset().filter(team=team).order_by("name")
        )

    def clean_filter_query_string(self):
        raw = self.cleaned_data.get("filter_query_string", "")
        if not raw:
            return raw
        try:
            params = FilterParams(QueryDict(raw))
        except Exception as e:  # noqa: BLE001 - surface as a form error
            raise forms.ValidationError(f"Invalid filter query: {e}") from e
        if not params.filters:
            raise forms.ValidationError(
                "Filter query is malformed: no complete column/operator/value triples found."
            )
        return raw

    def clean_source_experiment(self):
        experiment = self.cleaned_data.get("source_experiment")
        if experiment and experiment.team_id != self.team.id:
            raise forms.ValidationError("Source chatbot must belong to your team.")
        return experiment

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.team = self.team
        instance.dataset = self.dataset
        if commit:
            instance.save()
        return instance
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest apps/evaluations/tests/test_auto_population_form.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
uv run ruff check apps/evaluations/forms.py --fix
uv run ruff format apps/evaluations/forms.py
git add apps/evaluations/forms.py apps/evaluations/tests/test_auto_population_form.py
git commit -m "feat(evaluations): DatasetAutoPopulationRuleForm with team/filter validation"
```

---

## Task 15: CRUD views for `DatasetAutoPopulationRule`

**Files:**
- Create: `apps/evaluations/views/auto_population_views.py`
- Modify: `apps/evaluations/views/__init__.py` (if it re-exports)

- [ ] **Step 1: Write the views**

Create `apps/evaluations/views/auto_population_views.py`:

```python
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, UpdateView

from apps.evaluations.forms import DatasetAutoPopulationRuleForm
from apps.evaluations.models import DatasetAutoPopulationRule, EvaluationDataset
from apps.teams.decorators import login_and_team_required
from apps.teams.mixins import LoginAndTeamRequiredMixin


class _RuleViewMixin(LoginAndTeamRequiredMixin, PermissionRequiredMixin):
    permission_required = "evaluations.change_evaluationdataset"
    model = DatasetAutoPopulationRule
    form_class = DatasetAutoPopulationRuleForm
    template_name = "evaluations/auto_population_rule_form.html"

    def get_queryset(self):
        return DatasetAutoPopulationRule.objects.filter(team=self.request.team)

    def get_dataset(self):
        return get_object_or_404(
            EvaluationDataset, id=self.kwargs["dataset_id"], team=self.request.team
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["team"] = self.request.team
        kwargs["dataset"] = self.get_dataset()
        return kwargs

    def get_success_url(self):
        return reverse(
            "evaluations:dataset_edit",
            args=[self.request.team.slug, self.kwargs["dataset_id"]],
        )


class CreateAutoPopulationRule(_RuleViewMixin, CreateView):
    extra_context = {"page_title": "Add auto-population rule", "button_text": "Create rule"}

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Auto-population rule created.")
        return response


class EditAutoPopulationRule(_RuleViewMixin, UpdateView):
    extra_context = {"page_title": "Edit auto-population rule", "button_text": "Save"}

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Auto-population rule updated.")
        return response


class DeleteAutoPopulationRule(LoginAndTeamRequiredMixin, PermissionRequiredMixin, DeleteView):
    permission_required = "evaluations.change_evaluationdataset"
    model = DatasetAutoPopulationRule

    def get_queryset(self):
        return DatasetAutoPopulationRule.objects.filter(team=self.request.team)

    def delete(self, request, *args, **kwargs):
        self.get_object().delete()
        return HttpResponse(status=200)


@login_and_team_required
@permission_required("evaluations.change_evaluationdataset")
@require_POST
def toggle_auto_population_rule(request, team_slug: str, pk: int):
    rule = get_object_or_404(DatasetAutoPopulationRule, id=pk, team__slug=team_slug)
    rule.is_enabled = not rule.is_enabled
    if rule.is_enabled:
        rule.consecutive_failure_count = 0
        rule.last_error = ""
    rule.save(
        update_fields=["is_enabled", "consecutive_failure_count", "last_error"]
    )
    return redirect(
        reverse("evaluations:dataset_edit", args=[team_slug, rule.dataset_id])
    )
```

- [ ] **Step 2: Smoke check**

Run: `uv run python manage.py check`
Expected: no errors. (The URL names referenced are added in Task 16; the views file is currently unimported so the check passes as-is.)

- [ ] **Step 3: Commit**

```bash
uv run ruff check apps/evaluations/views --fix
uv run ruff format apps/evaluations/views
git add apps/evaluations/views/auto_population_views.py
git commit -m "feat(evaluations): rule CRUD views"
```

---

## Task 16: URL wiring for rules

**Files:**
- Modify: `apps/evaluations/urls.py`

- [ ] **Step 1: Add URL paths**

In `apps/evaluations/urls.py`, append (before the trailing `urlpatterns.extend(...)` calls):

```python
from .views import auto_population_views  # add to existing imports

# ... within urlpatterns, add:
    path(
        "dataset/<int:dataset_id>/auto_population/new/",
        auto_population_views.CreateAutoPopulationRule.as_view(),
        name="auto_population_rule_new",
    ),
    path(
        "dataset/<int:dataset_id>/auto_population/<int:pk>/",
        auto_population_views.EditAutoPopulationRule.as_view(),
        name="auto_population_rule_edit",
    ),
    path(
        "dataset/<int:dataset_id>/auto_population/<int:pk>/delete/",
        auto_population_views.DeleteAutoPopulationRule.as_view(),
        name="auto_population_rule_delete",
    ),
    path(
        "auto_population/<int:pk>/toggle/",
        auto_population_views.toggle_auto_population_rule,
        name="auto_population_rule_toggle",
    ),
```

- [ ] **Step 2: Smoke check**

Run: `uv run python manage.py check`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
uv run ruff check apps/evaluations/urls.py --fix
uv run ruff format apps/evaluations/urls.py
git add apps/evaluations/urls.py
git commit -m "feat(evaluations): URL routes for auto-population rules"
```

---

## Task 17: Auto-population rule form template + dataset-edit panel

**Files:**
- Create: `templates/evaluations/auto_population_rule_form.html`
- Create: `templates/evaluations/components/auto_population_rules_panel.html`
- Modify: `templates/evaluations/dataset_edit.html`
- Modify: `apps/evaluations/views/dataset_views.py` (`EditDataset.get_context_data`)

- [ ] **Step 1: Create the rule form template**

Create `templates/evaluations/auto_population_rule_form.html`:

```html
{% extends "web/app/app_base.html" %}
{% block app %}
  <div class="max-w-3xl mx-auto p-6">
    <h1 class="text-2xl font-semibold mb-4">{{ page_title }}</h1>
    <form method="post" novalidate>
      {% csrf_token %}
      {% include "generic/form_fields.html" with form=form %}

      <fieldset class="mt-6">
        <legend class="font-medium">Filter criteria</legend>
        <p class="text-sm text-gray-500 mb-2">
          Leave blank to ingest every session from the source chatbot. To use
          a filter, configure it on the dataset's session list and copy the
          query string from the URL.
        </p>
      </fieldset>

      <div class="mt-6 flex gap-2">
        <button type="submit" class="btn btn-primary">{{ button_text }}</button>
        <a href="{% url 'evaluations:dataset_edit' request.team.slug view.kwargs.dataset_id %}"
           class="btn btn-ghost">Cancel</a>
      </div>
    </form>
  </div>
{% endblock %}
```

(If `generic/form_fields.html` does not exist, render fields with `{% for field in form %}{{ field.label_tag }}{{ field }}{% endfor %}`. Confirm by listing `templates/generic/`.)

- [ ] **Step 2: Create the rules panel partial**

Create `templates/evaluations/components/auto_population_rules_panel.html`:

```html
<section class="mt-6 border rounded p-4">
  <header class="flex items-center justify-between mb-3">
    <h2 class="text-lg font-medium">Auto-population rules</h2>
    <a class="btn btn-sm btn-primary"
       href="{% url 'evaluations:auto_population_rule_new' request.team.slug dataset.id %}">
      Add rule
    </a>
  </header>

  {% if dataset.auto_population_rules.all %}
    <table class="w-full text-sm">
      <thead>
        <tr class="text-left">
          <th>Source chatbot</th>
          <th>Enabled</th>
          <th>Last run</th>
          <th>Status</th>
          <th>Last error</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {% for rule in dataset.auto_population_rules.all %}
          <tr>
            <td>{{ rule.source_experiment.name }}</td>
            <td>{{ rule.is_enabled|yesno:"Yes,No" }}</td>
            <td>{{ rule.last_run_at|default:"—" }}</td>
            <td>{{ rule.get_last_run_status_display|default:"—" }}</td>
            <td class="text-red-600">{{ rule.last_error|truncatechars:60 }}</td>
            <td>
              <a class="link"
                 href="{% url 'evaluations:auto_population_rule_edit' request.team.slug dataset.id rule.id %}">
                Edit
              </a>
              <form method="post"
                    action="{% url 'evaluations:auto_population_rule_toggle' request.team.slug rule.id %}"
                    class="inline">
                {% csrf_token %}
                <button type="submit" class="link">
                  {% if rule.is_enabled %}Disable{% else %}Enable{% endif %}
                </button>
              </form>
            </td>
          </tr>
        {% endfor %}
      </tbody>
    </table>
  {% else %}
    <p class="text-sm text-gray-500">No auto-population rules configured.</p>
  {% endif %}
</section>
```

- [ ] **Step 3: Include the panel in the dataset edit template**

In `templates/evaluations/dataset_edit.html`, add (in the main body, after the existing dataset edit form section — match indentation):

```html
{% include "evaluations/components/auto_population_rules_panel.html" with dataset=object %}
```

- [ ] **Step 4: Manual smoke**

Run: `uv run python manage.py check`
Expected: no errors. Optionally start the dev server and visit `/evaluations/dataset/<id>/` to confirm the panel renders and the "Add rule" link routes correctly.

- [ ] **Step 5: Commit**

```bash
git add templates/evaluations/auto_population_rule_form.html templates/evaluations/components/auto_population_rules_panel.html templates/evaluations/dataset_edit.html
git commit -m "feat(evaluations): rule form template and dataset-edit rules panel"
```

---

## Task 18: `auto_run_on_append` checkbox in `EvaluationConfigForm`

**Files:**
- Modify: `apps/evaluations/forms.py` (`EvaluationConfigForm.Meta.fields`)
- Modify: `templates/evaluations/evaluation_config_form.html`

- [ ] **Step 1: Add the field to the form**

In `apps/evaluations/forms.py`, locate `EvaluationConfigForm.Meta.fields` and append `"auto_run_on_append"`:

```python
        fields = [
            "name",
            "evaluators",
            "dataset",
            "experiment_version",
            "run_generation",
            "base_experiment",
            "auto_run_on_append",
        ]
```

- [ ] **Step 2: Render the checkbox in the template**

In `templates/evaluations/evaluation_config_form.html`, add (next to the other field rows):

```html
<div class="form-control mt-3">
  <label class="cursor-pointer label inline-flex items-center gap-2">
    {{ form.auto_run_on_append }}
    <span class="label-text">Auto-run this evaluation when new rows are auto-populated</span>
  </label>
  <p class="text-xs text-gray-500 mt-1">
    {{ form.auto_run_on_append.help_text }}
  </p>
</div>
```

(Inspect the file to match its existing field-row markup style; the snippet above is illustrative and may need to align with surrounding patterns.)

- [ ] **Step 3: Smoke check**

Run: `uv run python manage.py check`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
uv run ruff check apps/evaluations/forms.py --fix
uv run ruff format apps/evaluations/forms.py
git add apps/evaluations/forms.py templates/evaluations/evaluation_config_form.html
git commit -m "feat(evaluations): expose auto_run_on_append on the eval config form"
```

---

## Task 19: Run history shows DELTA badge + scope size

**Files:**
- Modify: `apps/evaluations/tables.py` (`EvaluationRunTable`)
- Possibly modify: `templates/evaluations/evaluation_run_status_column.html`

- [ ] **Step 1: Audit the existing table**

Run: `grep -n "type\|status_column\|class EvaluationRunTable" apps/evaluations/tables.py | head`
Confirm whether `type` is already exposed as a column. If not, add it:

```python
class EvaluationRunTable(tables.Table):
    type = tables.TemplateColumn(
        template_code=(
            "{% if record.type == 'delta' %}"
            "  <span class='badge badge-info'>delta · {{ record.scoped_messages.count }}</span>"
            "{% elif record.type == 'preview' %}"
            "  <span class='badge'>preview</span>"
            "{% else %}"
            "  <span class='badge badge-ghost'>full</span>"
            "{% endif %}"
        ),
        verbose_name="Type",
        orderable=False,
    )

    class Meta:
        # ... existing meta plus add "type" to sequence/fields
```

- [ ] **Step 2: Update the existing run-history template if needed**

If the table uses `template_name` rendering, ensure the new column is included in the template's column ordering.

- [ ] **Step 3: Smoke check**

Run: `uv run python manage.py check`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
uv run ruff check apps/evaluations/tables.py --fix
uv run ruff format apps/evaluations/tables.py
git add apps/evaluations/tables.py templates/evaluations/
git commit -m "feat(evaluations): show DELTA badge and scope size in run history"
```

---

## Task 20: Run results page scopes to `scoped_messages`

**Files:**
- Modify: `apps/evaluations/models.py` (`EvaluationRun.get_table_data`) **or** the results view
- Test: `apps/evaluations/tests/test_delta_evaluation_run.py`

- [ ] **Step 1: Write failing test**

Append to `apps/evaluations/tests/test_delta_evaluation_run.py`:

```python
from apps.evaluations.models import EvaluationResult
from apps.utils.factories.evaluations import EvaluatorFactory


@pytest.mark.django_db()
def test_get_table_data_delta_only_returns_scoped_messages():
    config = EvaluationConfigFactory.create()
    evaluator = EvaluatorFactory.create(team=config.team)
    config.evaluators.add(evaluator)

    in_scope = EvaluationMessageFactory.create()
    out_of_scope = EvaluationMessageFactory.create()
    config.dataset.messages.add(in_scope, out_of_scope)

    run = EvaluationRun.objects.create(
        team=config.team, config=config, type=EvaluationRunType.DELTA
    )
    run.scoped_messages.add(in_scope)

    EvaluationResult.objects.create(
        team=config.team,
        run=run,
        evaluator=evaluator,
        message=in_scope,
        output={"message": {"input": {"content": "hi"}, "output": {"content": "hello"}}},
    )
    EvaluationResult.objects.create(
        team=config.team,
        run=run,
        evaluator=evaluator,
        message=out_of_scope,
        output={"message": {"input": {"content": "n/a"}, "output": {"content": "n/a"}}},
    )

    rows = run.get_table_data()
    message_ids = {row["message_id"] for row in rows}
    assert message_ids == {in_scope.id}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest apps/evaluations/tests/test_delta_evaluation_run.py::test_get_table_data_delta_only_returns_scoped_messages -v`
Expected: FAIL — `get_table_data` currently returns rows for all results regardless of scope.

- [ ] **Step 3: Update `EvaluationRun.get_table_data`**

In `apps/evaluations/models.py`, near the start of `EvaluationRun.get_table_data`:

```python
    def get_table_data(self, include_ids: bool = False):
        results_qs = (
            self.results.select_related("message__session__experiment", "evaluator", "session")
            .prefetch_related("applied_tags__tag")
            .order_by("created_at")
        )
        if self.type == EvaluationRunType.DELTA and self.scoped_messages.exists():
            scoped_ids = self.scoped_messages.values_list("id", flat=True)
            results_qs = results_qs.filter(message_id__in=scoped_ids)

        results = results_qs.all()
        # ... existing body unchanged
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest apps/evaluations/tests/test_delta_evaluation_run.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Final integration test**

Run: `uv run pytest apps/evaluations/tests -v`
Expected: ALL PASS (no regressions in existing tests).

- [ ] **Step 6: Commit**

```bash
uv run ruff check apps/evaluations --fix
uv run ruff format apps/evaluations
git add apps/evaluations/models.py apps/evaluations/tests/test_delta_evaluation_run.py
git commit -m "feat(evaluations): run results page scopes to delta-run scoped_messages"
```

---

## Final integration

- [ ] **Run the whole evaluations test suite**

Run: `uv run pytest apps/evaluations -v`
Expected: ALL PASS.

- [ ] **Run lint + type-check on touched code**

```bash
uv run ruff check apps/evaluations apps/utils/factories config/settings.py --fix
uv run ruff format apps/evaluations apps/utils/factories config/settings.py
uv run ty check apps/evaluations
```

Expected: no new failures (some pre-existing repo issues may persist).

- [ ] **Manual end-to-end smoke** (optional, recommended)

1. Start services: `inv up`
2. Migrate: `uv run python manage.py migrate`
3. Run server: `uv run inv runserver`
4. Create a dataset, attach an auto-population rule pointing at any chatbot, attach an `auto_run_on_append=True` `EvaluationConfig` to the dataset, generate a session via the chat widget, manually invoke the task: `uv run python manage.py shell -c "from apps.evaluations.tasks import auto_populate_eval_datasets; auto_populate_eval_datasets()"`. Confirm new dataset rows and a DELTA `EvaluationRun`.

- [ ] **Open the PR** using `.github/pull_request_template.md`. Check the "migrations are backwards compatible" box. Note in the PR body that this PR depends on `flag_evaluations` being enabled.

---

## Spec coverage checklist

| Spec section | Implemented in |
|---|---|
| `DatasetAutoPopulationRule` schema | Task 5 |
| `EvaluationConfig.auto_run_on_append` | Task 2 |
| `EvaluationRunType.DELTA` | Task 1 |
| `EvaluationRun.scoped_messages` M2M | Task 1 |
| `flag_evaluations` gating (no new flag) | implicit (existing flag covers the views & schedule registration) |
| Migration additivity | Tasks 1, 2, 5, 13 |
| Periodic task & lookback semantics | Tasks 8, 9, 12, 13 |
| Forward-only floor via `rule.created_at` | Task 9 |
| NOT IN dedup (session and message mode) | Tasks 8, 9 |
| Failure handling + auto-disable | Tasks 7, 11 |
| Concurrency: `select_for_update(skip_locked=True)` | Task 12 |
| Auto-trigger only from auto-population path | Task 10 |
| `EvaluationConfig.run(scoped_messages=...)` | Task 3 |
| `run_evaluation_task` honours scope | Task 4 |
| Tagging on DELTA runs (no special branch) | Task 10 (no code change required: `_maybe_apply_tag_rules` only filters out PREVIEW) |
| Rule form validation (mode mismatch implicit, cross-team, malformed filter) | Task 14 |
| UI: rule list/edit on dataset detail | Tasks 15, 16, 17 |
| UI: `auto_run_on_append` on eval config | Task 18 |
| UI: run history badge + scope | Task 19 |
| UI: results scoped to scoped messages | Task 20 |

(Mode-mismatch is enforced at task time by selecting the right filter class via `dataset.evaluation_mode`. If a stricter form-level check is desired later, add it as a follow-up.)
