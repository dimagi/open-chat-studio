# List View Performance — Approach 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce p95 latency of the chatbot list, session list, trace list, and annotation-queue session list on data-heavy teams via indexes, query rewrites, and a bounded prefetch — measure-first before considering Approach 2.

**Architecture:** Twelve independently-shippable changes layered onto the existing Django/django-tables2 list views. Pattern: add Postgres indexes for the hot-path queries, replace the unconditional `.distinct()` in the dynamic-filter base class with targeted `EXISTS` subqueries in the few filters that traverse one-to-many relations, and slice the prefetch to the visible page. No schema reshape, no new tables, no behavioural changes for users.

**Tech Stack:** Django 5.x, django-tables2, Postgres (`pg_stat_statements`, `AddIndexConcurrently` for online index creation), pytest, factory-boy.

**Spec:** `docs/superpowers/specs/2026-04-30-list-view-performance-design.md`

---

## File map

**Modified:**
- `apps/trace/models.py` — add `Meta.indexes`
- `apps/trace/views.py` — replace `team__slug=...` with `team=...`
- `apps/experiments/models.py` — add `(team, -last_activity_at)` to `ExperimentSession.Meta.indexes`; tighten `get_table_queryset`
- `apps/web/dynamic_filters/base.py` — drop unconditional `.distinct()`
- `apps/experiments/filters.py` — rewrite `ChatMessageTagsFilter` operators as `EXISTS`; add `MessageTimestampFilter` subclass
- `apps/trace/filters.py` — rewrite `MessageTagsFilter` operators as `EXISTS`, drop manual `.distinct()`
- `apps/chatbots/views.py` — `ChatbotSessionsTableView.get_table_data` — page-bounded prefetch
- `apps/human_annotations/views/queue_views.py` — page-bounded prefetch on `AnnotationQueueSessionsTableView`

**Created:**
- `apps/trace/migrations/0012_trace_indexes.py`
- `apps/experiments/migrations/0134_expsession_team_lastactivity_idx.py`
- New tests appended to `apps/experiments/tests/test_filters.py` and `apps/trace/tests/test_filters.py`
- A short before/after measurement note in `docs/superpowers/specs/2026-04-30-list-view-performance-results.md` (collected as work progresses)

Each task below produces one commit.

---

## Task 1: Add `Trace.Meta.indexes` and concurrent migration

**Files:**
- Modify: `apps/trace/models.py:18` (`Trace` model)
- Create: `apps/trace/migrations/0012_trace_indexes.py`
- Test: `apps/trace/tests/test_models.py` (append)

- [ ] **Step 1: Add a regression test for index presence**

Append to `apps/trace/tests/test_models.py`:

```python
from django.db import connection

import pytest


@pytest.mark.django_db()
def test_trace_expected_indexes_exist():
    """Smoke check that the indexes the list-view hot path depends on are present."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'trace_trace'"
        )
        names = {row[0] for row in cursor.fetchall()}

    assert "trace_team_timestamp_idx" in names
    assert "trace_experiment_timestamp_idx" in names
    assert "trace_session_timestamp_idx" in names
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest apps/trace/tests/test_models.py::test_trace_expected_indexes_exist -v`

Expected: FAIL — none of the three index names exist yet.

- [ ] **Step 3: Add `Meta.indexes` to the `Trace` model**

In `apps/trace/models.py`, add `from django.db.models import Q` to the imports at the top, then add a `Meta` class to `Trace`:

```python
class Trace(models.Model):
    # ... existing fields unchanged ...

    class Meta:
        indexes = [
            models.Index(
                fields=["team", "-timestamp"],
                name="trace_team_timestamp_idx",
                condition=~Q(status="pending"),
            ),
            models.Index(
                fields=["experiment", "-timestamp"],
                name="trace_experiment_timestamp_idx",
            ),
            models.Index(
                fields=["session", "-timestamp"],
                name="trace_session_timestamp_idx",
            ),
        ]
```

- [ ] **Step 4: Generate the migration**

Run: `uv run python manage.py makemigrations trace --name trace_indexes`

This produces `apps/trace/migrations/0012_trace_indexes.py`. It will be a regular `AddIndex` migration — we need to convert it to concurrent.

- [ ] **Step 5: Convert the migration to concurrent operations**

Open `apps/trace/migrations/0012_trace_indexes.py` and replace its contents with:

```python
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("trace", "0011_add_trace_metrics"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="trace",
            index=models.Index(
                fields=["team", "-timestamp"],
                name="trace_team_timestamp_idx",
                condition=~Q(status="pending"),
            ),
        ),
        AddIndexConcurrently(
            model_name="trace",
            index=models.Index(
                fields=["experiment", "-timestamp"],
                name="trace_experiment_timestamp_idx",
            ),
        ),
        AddIndexConcurrently(
            model_name="trace",
            index=models.Index(
                fields=["session", "-timestamp"],
                name="trace_session_timestamp_idx",
            ),
        ),
    ]
```

`atomic = False` is required because Postgres `CREATE INDEX CONCURRENTLY` cannot run inside a transaction.

- [ ] **Step 6: Apply the migration and run tests**

Run: `uv run python manage.py migrate trace`
Run: `uv run pytest apps/trace/tests/test_models.py::test_trace_expected_indexes_exist -v`

Expected: PASS.

Run: `uv run pytest apps/trace/ -v`

Expected: existing trace tests still pass.

- [ ] **Step 7: Lint, type-check, commit**

Run: `uv run ruff check apps/trace/models.py apps/trace/migrations/0012_trace_indexes.py apps/trace/tests/test_models.py --fix`
Run: `uv run ruff format apps/trace/models.py apps/trace/migrations/0012_trace_indexes.py apps/trace/tests/test_models.py`
Run: `uv run ty check apps/trace/`

```bash
git add apps/trace/models.py apps/trace/migrations/0012_trace_indexes.py apps/trace/tests/test_models.py
git commit -m "perf(trace): add team/experiment/session × timestamp indexes"
```

---

## Task 2: Replace `team__slug` join with `team_id` in TraceTableView

**Files:**
- Modify: `apps/trace/views.py:40-46`
- Test: `apps/trace/tests/test_views.py` (create)

- [ ] **Step 1: Create a view-level test**

Create `apps/trace/tests/test_views.py`:

```python
from django.urls import reverse

import pytest

from apps.trace.models import TraceStatus
from apps.utils.factories.experiment import ExperimentFactory, ParticipantFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.traces import TraceFactory


@pytest.mark.django_db()
def test_trace_table_view_returns_only_team_traces(client, team_with_users):
    """Smoke check that the trace list view filters by team and renders successfully."""
    team = team_with_users
    user = team.members.first()
    other_team = TeamFactory.create()

    experiment = ExperimentFactory.create(team=team)
    participant = ParticipantFactory.create(team=team)
    own_trace = TraceFactory.create(
        team=team, experiment=experiment, participant=participant,
        status=TraceStatus.SUCCESS, duration=1000,
    )
    foreign_trace = TraceFactory.create(
        team=other_team,
        experiment=ExperimentFactory.create(team=other_team),
        participant=ParticipantFactory.create(team=other_team),
        status=TraceStatus.SUCCESS, duration=1000,
    )

    client.force_login(user)
    response = client.get(reverse("trace:table", args=[team.slug]))

    assert response.status_code == 200
    visible_ids = {row.record.id for row in response.context_data["table"].rows}
    assert own_trace.id in visible_ids
    assert foreign_trace.id not in visible_ids
```

- [ ] **Step 2: Run the test to verify it passes against the current code**

Run: `uv run pytest apps/trace/tests/test_views.py -v`

Expected: PASS. We are characterising existing behaviour before refactoring.

- [ ] **Step 3: Replace the `team__slug` lookup**

In `apps/trace/views.py`, change `TraceTableView.get_queryset`:

```python
class TraceTableView(LoginAndTeamRequiredMixin, PermissionRequiredMixin, SingleTableView):
    template_name = "table/single_table.html"
    model = Trace
    table_class = TraceTable
    permission_required = "trace.view_trace"

    def get_queryset(self):
        queryset = (
            Trace.objects.select_related("participant", "experiment", "session")
            .filter(team=self.request.team)
            .exclude(status=TraceStatus.PENDING)
            .order_by("-timestamp")
        )

        timezone = self.request.session.get("detected_tz", None)
        trace_filter = TraceFilter()
        return trace_filter.apply(queryset, filter_params=FilterParams.from_request(self.request), timezone=timezone)
```

The single change is `team__slug=self.request.team.slug` → `team=self.request.team`.

- [ ] **Step 4: Run the test to verify it still passes**

Run: `uv run pytest apps/trace/tests/test_views.py -v`

Expected: PASS.

- [ ] **Step 5: Lint and commit**

Run: `uv run ruff check apps/trace/views.py apps/trace/tests/test_views.py --fix`
Run: `uv run ruff format apps/trace/views.py apps/trace/tests/test_views.py`

```bash
git add apps/trace/views.py apps/trace/tests/test_views.py
git commit -m "perf(trace): use team= instead of team__slug= in TraceTableView"
```

---

## Task 3: Add `(team, -last_activity_at)` index on ExperimentSession

**Files:**
- Modify: `apps/experiments/models.py:1446-1448` (`ExperimentSession.Meta`)
- Create: `apps/experiments/migrations/0134_expsession_team_lastactivity_idx.py`
- Test: `apps/experiments/tests/test_models.py` (append)

- [ ] **Step 1: Add a regression test for index presence**

Append to `apps/experiments/tests/test_models.py`:

```python
from django.db import connection

import pytest


@pytest.mark.django_db()
def test_experimentsession_team_lastactivity_index_exists():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'experiments_experimentsession'"
        )
        names = {row[0] for row in cursor.fetchall()}

    assert "expsession_team_lastactivity_idx" in names
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest apps/experiments/tests/test_models.py::test_experimentsession_team_lastactivity_index_exists -v`

Expected: FAIL — index does not exist.

- [ ] **Step 3: Add the index to `ExperimentSession.Meta`**

In `apps/experiments/models.py`, update the `ExperimentSession.Meta` class:

```python
    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["chat", "team"]),
            models.Index(fields=["chat", "team", "ended_at"]),
            models.Index(fields=["team", "-last_activity_at"], name="expsession_team_lastactivity_idx"),
        ]
```

- [ ] **Step 4: Generate the migration**

Run: `uv run python manage.py makemigrations experiments --name expsession_team_lastactivity_idx`

This produces `apps/experiments/migrations/0134_expsession_team_lastactivity_idx.py`.

- [ ] **Step 5: Convert the migration to concurrent**

Replace the contents of `apps/experiments/migrations/0134_expsession_team_lastactivity_idx.py` with:

```python
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("experiments", "0133_alter_syntheticvoice_service"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="experimentsession",
            index=models.Index(
                fields=["team", "-last_activity_at"],
                name="expsession_team_lastactivity_idx",
            ),
        ),
    ]
```

- [ ] **Step 6: Apply, run tests, commit**

Run: `uv run python manage.py migrate experiments`
Run: `uv run pytest apps/experiments/tests/test_models.py::test_experimentsession_team_lastactivity_index_exists -v`

Expected: PASS.

Run: `uv run ruff check apps/experiments/models.py apps/experiments/migrations/0134_expsession_team_lastactivity_idx.py apps/experiments/tests/test_models.py --fix`
Run: `uv run ruff format apps/experiments/models.py apps/experiments/migrations/0134_expsession_team_lastactivity_idx.py apps/experiments/tests/test_models.py`

```bash
git add apps/experiments/models.py apps/experiments/migrations/0134_expsession_team_lastactivity_idx.py apps/experiments/tests/test_models.py
git commit -m "perf(experiments): add (team, -last_activity_at) index on ExperimentSession"
```

---

## Task 4: Add no-duplicate regression tests for `ChatMessageTagsFilter`

This task only adds tests. They pass under the current `DISTINCT`-everything code; they are the safety net for tasks 5 and 9.

**Files:**
- Modify: `apps/experiments/tests/test_filters.py`

- [ ] **Step 1: Add a fixture and three tests**

Append to `apps/experiments/tests/test_filters.py`, inside `class TestExperimentSessionFilters` (above the existing tests):

```python
    @pytest.fixture()
    def session_with_many_message_tags(self):
        """One session whose chat contains multiple messages, each with multiple tags.

        A naive JOIN-based filter on `chat__messages__tags__name` returns the
        session N×M times (N matching messages × M matching tags) — a regression
        guard for the EXISTS rewrite.
        """
        session = ExperimentSessionFactory.create()
        team = session.team
        important = _get_tag(team=team, name="important")
        urgent = _get_tag(team=team, name="urgent")

        for content in ("first", "second", "third"):
            msg = ChatMessage.objects.create(
                chat=session.chat, content=content, message_type=ChatMessageType.HUMAN,
            )
            msg.add_tags([important, urgent], team=team, added_by=None)

        return session, [important, urgent]

    def test_message_tags_any_of_returns_no_duplicates(self, session_with_many_message_tags):
        session, _ = session_with_many_message_tags
        params = {
            "filter_0_column": "tags",
            "filter_0_operator": Operators.ANY_OF,
            "filter_0_value": json.dumps(["important", "urgent"]),
        }
        filtered = ExperimentSessionFilter().apply(
            session.experiment.sessions.all(), FilterParams(_get_querydict(params))
        )
        assert filtered.count() == 1
        assert list(filtered) == [session]

    def test_message_tags_all_of_returns_no_duplicates(self, session_with_many_message_tags):
        session, _ = session_with_many_message_tags
        params = {
            "filter_0_column": "tags",
            "filter_0_operator": Operators.ALL_OF,
            "filter_0_value": json.dumps(["important", "urgent"]),
        }
        filtered = ExperimentSessionFilter().apply(
            session.experiment.sessions.all(), FilterParams(_get_querydict(params))
        )
        assert filtered.count() == 1
        assert list(filtered) == [session]

    def test_message_tags_excludes_returns_no_duplicates(self, session_with_many_message_tags):
        """Exclude a tag that no message has — both sessions should remain, each exactly once.

        The bug being guarded against is `session` appearing 3 times (one per
        tagged message) when the global `.distinct()` is removed.
        """
        session, _ = session_with_many_message_tags
        other = ExperimentSessionFactory.create(experiment=session.experiment)

        params = {
            "filter_0_column": "tags",
            "filter_0_operator": Operators.EXCLUDES,
            "filter_0_value": json.dumps(["nonexistent"]),
        }
        filtered = ExperimentSessionFilter().apply(
            session.experiment.sessions.all(), FilterParams(_get_querydict(params))
        )
        assert filtered.count() == 2
        assert set(filtered) == {session, other}
```

- [ ] **Step 2: Run the new tests to verify they pass under current code**

Run: `uv run pytest apps/experiments/tests/test_filters.py -v -k "no_duplicates"`

Expected: PASS (current code's unconditional `.distinct()` masks duplicates).

- [ ] **Step 3: Lint and commit**

Run: `uv run ruff check apps/experiments/tests/test_filters.py --fix`
Run: `uv run ruff format apps/experiments/tests/test_filters.py`

```bash
git add apps/experiments/tests/test_filters.py
git commit -m "test(experiments): add no-duplicate regression tests for ChatMessageTagsFilter"
```

---

## Task 5: Rewrite `ChatMessageTagsFilter.apply_any_of` and `apply_excludes` as EXISTS

**Files:**
- Modify: `apps/experiments/filters.py:60-93`
- Test: existing tests from Task 4 + Task 9

- [ ] **Step 1: Rewrite `apply_any_of` and `apply_excludes` to use `Exists` subqueries**

In `apps/experiments/filters.py`, replace `ChatMessageTagsFilter.apply_any_of` and `apply_excludes` with:

```python
    def apply_any_of(self, queryset, value, timezone=None):
        chat_content_type = ContentType.objects.get_for_model(Chat)
        chat_message_content_type = ContentType.objects.get_for_model(ChatMessage)
        chat_tag_exists = Exists(
            CustomTaggedItem.objects.filter(
                object_id=OuterRef("chat_id"),
                content_type_id=chat_content_type.id,
                tag__name__in=value,
            )
        )
        message_tag_exists = Exists(
            CustomTaggedItem.objects.filter(
                content_type_id=chat_message_content_type.id,
                tag__name__in=value,
                object_id__in=Subquery(
                    ChatMessage.objects.filter(chat_id=OuterRef(OuterRef("chat_id"))).values("id")
                ),
            )
        )
        return queryset.filter(chat_tag_exists | message_tag_exists)

    def apply_excludes(self, queryset, value, timezone=None):
        chat_content_type = ContentType.objects.get_for_model(Chat)
        chat_message_content_type = ContentType.objects.get_for_model(ChatMessage)
        chat_tag_exists = Exists(
            CustomTaggedItem.objects.filter(
                object_id=OuterRef("chat_id"),
                content_type_id=chat_content_type.id,
                tag__name__in=value,
            )
        )
        message_tag_exists = Exists(
            CustomTaggedItem.objects.filter(
                content_type_id=chat_message_content_type.id,
                tag__name__in=value,
                object_id__in=Subquery(
                    ChatMessage.objects.filter(chat_id=OuterRef(OuterRef("chat_id"))).values("id")
                ),
            )
        )
        return queryset.exclude(chat_tag_exists | message_tag_exists)
```

`apply_all_of` is unchanged — it already uses `Exists`. The imports `ContentType, Exists, OuterRef, Subquery, CustomTaggedItem, Chat, ChatMessage` are already present in this file.

- [ ] **Step 2: Run the regression tests from Task 4 and the existing tag tests**

Run: `uv run pytest apps/experiments/tests/test_filters.py -v -k "tag"`

Expected: PASS — all tag-related tests, including the no-duplicate regressions, still pass.

- [ ] **Step 3: Lint, type-check, commit**

Run: `uv run ruff check apps/experiments/filters.py --fix`
Run: `uv run ruff format apps/experiments/filters.py`
Run: `uv run ty check apps/experiments/`

```bash
git add apps/experiments/filters.py
git commit -m "perf(experiments): rewrite ChatMessageTagsFilter any_of/excludes as EXISTS"
```

---

## Task 6: Add `MessageTimestampFilter` (EXISTS) and swap usage in ExperimentSessionFilter

The current "Message Date" timestamp filter applies on column `chat__messages__created_at` — a one-to-many traversal that multiplies rows. We add a dedicated subclass that uses `EXISTS` and replace the one usage.

**Files:**
- Modify: `apps/web/dynamic_filters/column_filters.py:82-141` (`TimestampFilter` import path stays the same; new subclass lives next to it)
- Modify: `apps/experiments/filters.py:200-205` (the `TimestampFilter("Message Date", ...)` instantiation)
- Test: `apps/experiments/tests/test_filters.py` (append)

- [ ] **Step 1: Add a no-duplicate regression test for `message_date` filter**

Append to `apps/experiments/tests/test_filters.py`, inside `class TestExperimentSessionFilters`:

```python
    def test_message_date_filter_returns_no_duplicates(self, session_with_many_message_tags):
        session, _ = session_with_many_message_tags
        # All three messages were created at "now"; filter for that date.
        today = timezone.now().date().isoformat()

        params = {
            "filter_0_column": "message_date",
            "filter_0_operator": Operators.ON,
            "filter_0_value": today,
        }
        filtered = ExperimentSessionFilter().apply(
            session.experiment.sessions.all(), FilterParams(_get_querydict(params))
        )
        assert filtered.count() == 1
        assert list(filtered) == [session]
```

The fixture `session_with_many_message_tags` already creates three messages on the same day; this guarantees a one-to-many JOIN would yield 3 rows.

- [ ] **Step 2: Run it — should pass under current `.distinct()`**

Run: `uv run pytest apps/experiments/tests/test_filters.py::TestExperimentSessionFilters::test_message_date_filter_returns_no_duplicates -v`

Expected: PASS.

- [ ] **Step 3: Add `MessageTimestampFilter` next to `TimestampFilter`**

In `apps/web/dynamic_filters/column_filters.py`, after the `TimestampFilter` class, add:

```python
class MessageTimestampFilter(TimestampFilter):
    """Timestamp filter that traverses chat__messages without multiplying rows.

    Uses `Exists(ChatMessage.objects.filter(...))` instead of a JOIN through
    `chat__messages__created_at`, so callers do not need a trailing `.distinct()`.
    """

    def _exists(self, **chatmessage_lookups):
        # Local import — avoids a top-level chat→dynamic_filters cycle.
        from django.db.models import Exists, OuterRef  # noqa: PLC0415
        from apps.chat.models import ChatMessage  # noqa: PLC0415

        return Exists(
            ChatMessage.objects.filter(chat_id=OuterRef("chat_id"), **chatmessage_lookups)
        )

    def apply_on(self, queryset, value, timezone=None) -> QuerySet:
        if date_value := self._get_date_as_utc(value):
            return queryset.filter(self._exists(created_at__date=date_value))
        return queryset

    def apply_before(self, queryset, value, timezone=None) -> QuerySet:
        if date_value := self._get_date_as_utc(value):
            return queryset.filter(self._exists(created_at__date__lt=date_value))
        return queryset

    def apply_after(self, queryset, value, timezone=None) -> QuerySet:
        if date_value := self._get_date_as_utc(value):
            return queryset.filter(self._exists(created_at__date__gt=date_value))
        return queryset

    def apply_range(self, queryset, value, timezone=None) -> QuerySet:
        try:
            client_tz = pytz.timezone(timezone) if timezone else pytz.UTC
            now_client = datetime.now(client_tz)
            if not value.endswith(("h", "d", "m")):
                return queryset
            num = int(value[:-1])
            unit = value[-1]
            if unit == "h":
                delta = timedelta(hours=num)
            elif unit == "d":
                delta = timedelta(days=num)
            elif unit == "m":
                delta = timedelta(minutes=num)
            else:
                return queryset
            range_starting_utc_time = (now_client - delta).astimezone(pytz.UTC)
            return queryset.filter(self._exists(created_at__gte=range_starting_utc_time))
        except (ValueError, TypeError, pytz.UnknownTimeZoneError):
            return queryset
```

Notes:
- The local import inside `_exists` avoids creating a top-level `chat → dynamic_filters` cycle. Top-level imports are the project default; the local import is a deliberate exception per AGENTS.md.
- The class assumes the queryset is over a model with a `chat_id` foreign key. This is the only place we need it (`ExperimentSession`); generalising further is YAGNI.

- [ ] **Step 4: Use the new filter in `ExperimentSessionFilter`**

In `apps/experiments/filters.py`, change the import:

```python
from apps.web.dynamic_filters.column_filters import (
    ExperimentFilter,
    MessageTimestampFilter,
    ParticipantFilter,
    RemoteIdFilter,
    SessionIdFilter,
    SessionStatusFilter,
    TimestampFilter,
)
```

Then replace the "Message Date" `TimestampFilter` instantiation in `ExperimentSessionFilter.filters`:

```python
        MessageTimestampFilter(
            label="Message Date",
            column="chat__messages__created_at",  # retained for schema/UX, unused at query time
            query_param="message_date",
            description="Filter by message date",
        ),
```

The other two `TimestampFilter` instantiations (`last_message`, `first_message`) target columns directly on `ExperimentSession` and remain unchanged.

- [ ] **Step 5: Run the regression test**

Run: `uv run pytest apps/experiments/tests/test_filters.py::TestExperimentSessionFilters::test_message_date_filter_returns_no_duplicates -v`

Expected: PASS.

Run: `uv run pytest apps/experiments/tests/test_filters.py -v`

Expected: PASS — all session filter tests still pass.

- [ ] **Step 6: Lint, type-check, commit**

Run: `uv run ruff check apps/web/dynamic_filters/column_filters.py apps/experiments/filters.py apps/experiments/tests/test_filters.py --fix`
Run: `uv run ruff format apps/web/dynamic_filters/column_filters.py apps/experiments/filters.py apps/experiments/tests/test_filters.py`
Run: `uv run ty check apps/web/dynamic_filters/ apps/experiments/`

```bash
git add apps/web/dynamic_filters/column_filters.py apps/experiments/filters.py apps/experiments/tests/test_filters.py
git commit -m "perf(filters): EXISTS-based MessageTimestampFilter for chat__messages traversal"
```

---

## Task 7: Add no-duplicate regression tests for trace `MessageTagsFilter`

**Files:**
- Modify: `apps/trace/tests/test_filters.py`

- [ ] **Step 1: Add a fixture and three tests**

Append inside `class TestTraceFilter` in `apps/trace/tests/test_filters.py`:

```python
    @pytest.fixture()
    def trace_with_many_message_tags(self, team, experiment, participant):
        """A trace whose input_message has multiple tags — would multiply under JOIN."""
        from apps.chat.models import Chat  # noqa: PLC0415
        chat = Chat.objects.create(team=team, name="t")
        input_message = ChatMessage.objects.create(
            chat=chat, content="hi", message_type=ChatMessageType.HUMAN,
        )
        important = Tag.objects.create(team=team, name="important")
        urgent = Tag.objects.create(team=team, name="urgent")
        input_message.add_tags([important, urgent], team=team, added_by=None)

        trace = TraceFactory.create(
            team=team, experiment=experiment, participant=participant,
            status=TraceStatus.SUCCESS, duration=1000,
            input_message=input_message,
        )
        return trace

    def test_trace_message_tags_any_of_no_duplicates(self, trace_with_many_message_tags, team):
        queryset = Trace.objects.filter(team=team)
        result = self._create_filter_and_apply(
            queryset, "message_tags", Operators.ANY_OF,
            json.dumps(["important", "urgent"]),
        )
        assert result.count() == 1
        assert list(result) == [trace_with_many_message_tags]

    def test_trace_message_tags_all_of_no_duplicates(self, trace_with_many_message_tags, team):
        queryset = Trace.objects.filter(team=team)
        result = self._create_filter_and_apply(
            queryset, "message_tags", Operators.ALL_OF,
            json.dumps(["important", "urgent"]),
        )
        assert result.count() == 1
        assert list(result) == [trace_with_many_message_tags]

    def test_trace_message_tags_excludes_no_duplicates(self, trace_with_many_message_tags, team):
        """Exclude a tag that no message has — the trace should remain exactly once.

        The bug being guarded against is the trace appearing 2 times (one per
        tag on its input_message) when the global `.distinct()` is removed.
        """
        queryset = Trace.objects.filter(team=team)
        result = self._create_filter_and_apply(
            queryset, "message_tags", Operators.EXCLUDES,
            json.dumps(["nonexistent"]),
        )
        assert result.count() == 1
        assert list(result) == [trace_with_many_message_tags]
```

- [ ] **Step 2: Run them — should pass under current code**

Run: `uv run pytest apps/trace/tests/test_filters.py -v -k "no_duplicates"`

Expected: PASS.

- [ ] **Step 3: Lint and commit**

Run: `uv run ruff check apps/trace/tests/test_filters.py --fix`
Run: `uv run ruff format apps/trace/tests/test_filters.py`

```bash
git add apps/trace/tests/test_filters.py
git commit -m "test(trace): add no-duplicate regression tests for MessageTagsFilter"
```

---

## Task 8: Rewrite trace `MessageTagsFilter` operators as EXISTS

**Files:**
- Modify: `apps/trace/filters.py:35-60`

- [ ] **Step 1: Rewrite all three operator methods to use `EXISTS`**

In `apps/trace/filters.py`, replace `MessageTagsFilter` with:

```python
class MessageTagsFilter(ChoiceColumnFilter):
    query_param: str = "message_tags"
    label: str = "Message Tags"
    type: str = TYPE_CHOICE

    def prepare(self, team, **_):
        self.options = list(
            team.tag_set.filter(is_system_tag=False).values_list("name", flat=True).order_by("name").distinct()
        )

    def _exists(self, message_field: str, tag_names: list[str]):
        """Build an EXISTS clause matching tags on the message referenced by `message_field`."""
        from django.contrib.contenttypes.models import ContentType  # noqa: PLC0415
        from django.db.models import Exists, OuterRef  # noqa: PLC0415
        from apps.annotations.models import CustomTaggedItem  # noqa: PLC0415
        from apps.chat.models import ChatMessage  # noqa: PLC0415

        chat_message_content_type = ContentType.objects.get_for_model(ChatMessage)
        return Exists(
            CustomTaggedItem.objects.filter(
                content_type_id=chat_message_content_type.id,
                tag__name__in=tag_names,
                object_id=OuterRef(message_field),
            )
        )

    def apply_any_of(self, queryset, value, timezone=None):
        return queryset.filter(self._exists("input_message_id", value) | self._exists("output_message_id", value))

    def apply_all_of(self, queryset, value, timezone=None):
        for tag in value:
            queryset = queryset.filter(
                self._exists("input_message_id", [tag]) | self._exists("output_message_id", [tag])
            )
        return queryset

    def apply_excludes(self, queryset, value, timezone=None):
        return queryset.exclude(self._exists("input_message_id", value) | self._exists("output_message_id", value))
```

Notes:
- The previous implementation called `.distinct()` explicitly on `apply_any_of` and `apply_all_of`. Both calls are removed — `EXISTS` does not multiply rows.
- The previous `apply_excludes` did not call `.distinct()` but did inherit the global one (Task 9 removes that). The `EXISTS` form is still correct: "exclude rows where any matching tag exists".
- Local imports keep us out of an annotations→trace cycle.

- [ ] **Step 2: Run the regression tests from Task 7 and the existing tests**

Run: `uv run pytest apps/trace/tests/test_filters.py -v`

Expected: PASS — all trace filter tests, including the no-duplicate regressions, pass.

- [ ] **Step 3: Lint, type-check, commit**

Run: `uv run ruff check apps/trace/filters.py --fix`
Run: `uv run ruff format apps/trace/filters.py`
Run: `uv run ty check apps/trace/`

```bash
git add apps/trace/filters.py
git commit -m "perf(trace): rewrite MessageTagsFilter operators as EXISTS, drop manual distinct()"
```

---

## Task 9: Remove unconditional `.distinct()` from `MultiColumnFilter.apply`

This is the load-bearing change. Tasks 5/6/8 prepared every row-multiplying filter so this is now safe.

**Files:**
- Modify: `apps/web/dynamic_filters/base.py:105-112`
- Test: `apps/web/dynamic_filters/tests/test_base.py` (create)

- [ ] **Step 1: Create a SQL-shape test**

Create `apps/web/dynamic_filters/tests/__init__.py` if it does not exist (empty file).

Create `apps/web/dynamic_filters/tests/test_base.py`:

```python
import pytest

from apps.experiments.filters import ExperimentSessionFilter
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.web.dynamic_filters.datastructures import FilterParams


@pytest.mark.django_db()
def test_apply_does_not_emit_distinct_when_no_filters_applied():
    """The filter base class must not unconditionally add SELECT DISTINCT."""
    session = ExperimentSessionFactory.create()
    queryset = session.experiment.sessions.all()
    filtered = ExperimentSessionFilter().apply(queryset, FilterParams())
    sql = str(filtered.query).upper()
    assert "DISTINCT" not in sql, sql
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest apps/web/dynamic_filters/tests/test_base.py -v`

Expected: FAIL — current code emits `SELECT DISTINCT`.

- [ ] **Step 3: Drop the unconditional `.distinct()`**

In `apps/web/dynamic_filters/base.py`, change `MultiColumnFilter.apply`:

```python
    def apply(self, queryset: QuerySet, filter_params: FilterParams, timezone=None) -> QuerySet:
        """Applies the filters to the given queryset based on the `self.filter_params`."""
        queryset = self.prepare_queryset(queryset)

        for filter_component in self.filters:
            queryset = filter_component.apply(queryset, filter_params, timezone)

        return queryset
```

Single-line removal: drop `.distinct()` from the trailing `return`.

- [ ] **Step 4: Run the SQL-shape test, the per-filter regression tests, and the broader filter tests**

Run: `uv run pytest apps/web/dynamic_filters/tests/test_base.py -v`

Expected: PASS.

Run: `uv run pytest apps/experiments/tests/test_filters.py apps/trace/tests/test_filters.py apps/human_annotations/tests/ -v`

Expected: PASS — all four downstream filter test suites, including the no-duplicate regressions added in Tasks 4, 6, and 7, pass without DISTINCT.

- [ ] **Step 5: Lint, type-check, commit**

Run: `uv run ruff check apps/web/dynamic_filters/ --fix`
Run: `uv run ruff format apps/web/dynamic_filters/`
Run: `uv run ty check apps/web/dynamic_filters/`

```bash
git add apps/web/dynamic_filters/base.py apps/web/dynamic_filters/tests/__init__.py apps/web/dynamic_filters/tests/test_base.py
git commit -m "perf(filters): drop unconditional .distinct() in MultiColumnFilter.apply"
```

---

## Task 10: Bound `CustomTaggedItem` prefetch to the visible page

The session list's prefetch fires across the whole filtered queryset before pagination. Move the prefetch onto the page slice — the same shape `ChatbotExperimentTableView` already uses for its trend data.

**Files:**
- Modify: `apps/experiments/models.py:1395-1407` (`get_table_queryset`) — drop the prefetch
- Modify: `apps/chatbots/views.py:509-533` (`ChatbotSessionsTableView`) — add `get_table_data` with page-bounded prefetch
- Modify: `apps/human_annotations/views/queue_views.py:218-238` (`AnnotationQueueSessionsTableView`) — same shape
- Test: `apps/chatbots/tests/test_chatbot_views.py` (append)

- [ ] **Step 1: Write a query-count assertion test**

Append to `apps/chatbots/tests/test_chatbot_views.py`:

```python
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.annotations.models import Tag


@pytest.mark.django_db()
def test_session_table_prefetch_is_page_bounded(team_with_users):
    """Adding more sessions than fit on one page must not increase the number of
    queries fired for the tag prefetch — the prefetch should target only the
    sessions visible on the current page."""
    team = team_with_users
    user = team.members.first()
    experiment = ExperimentFactory.create(team=team)

    # Two sessions, both tagged. Page size is 25 by default; both fit on one page.
    tag = Tag.objects.create(team=team, name="t")
    for _ in range(2):
        s = ExperimentSessionFactory.create(team=team, experiment=experiment)
        s.chat.add_tag(tag, team=team, added_by=None)

    factory = RequestFactory()

    def do_request():
        request = factory.get(
            reverse("chatbots:sessions-list",
                    kwargs={"team_slug": team.slug, "experiment_id": experiment.id})
        )
        request.user = user
        request.team = team
        request.team_membership = get_team_membership_for_request(request)
        attach_session_middleware_to_request(request)
        set_current_team(team)
        view = ChatbotSessionsTableView.as_view()
        response = view(request, team_slug=team.slug, experiment_id=experiment.id)
        # Force evaluation of the table data
        list(response.context_data["table"].rows)
        return response

    with CaptureQueriesContext(connection) as ctx_two:
        do_request()
    queries_for_two = len(ctx_two.captured_queries)

    # Add another 30 tagged sessions — far beyond one page.
    for _ in range(30):
        s = ExperimentSessionFactory.create(team=team, experiment=experiment)
        s.chat.add_tag(tag, team=team, added_by=None)

    with CaptureQueriesContext(connection) as ctx_many:
        do_request()
    queries_for_many = len(ctx_many.captured_queries)

    # Adding off-page rows must not bloat the prefetch.
    # Allow a small slack for the COUNT(*) plan growing slightly.
    assert queries_for_many <= queries_for_two + 1, (
        f"Prefetch is not page-bounded: 2 sessions = {queries_for_two} queries, "
        f"32 sessions = {queries_for_many} queries"
    )
```

- [ ] **Step 2: Run the test — should fail today**

Run: `uv run pytest apps/chatbots/tests/test_chatbot_views.py::test_session_table_prefetch_is_page_bounded -v`

Expected: FAIL — query count grows with off-page sessions because the prefetch is unbounded.

- [ ] **Step 3: Drop the prefetch from `get_table_queryset`**

In `apps/experiments/models.py`, change `ExperimentSessionObjectManager.get_table_queryset`:

```python
    def get_table_queryset(self, team, experiment_id=None):
        queryset = self.get_queryset().filter(team=team)
        if experiment_id:
            queryset = queryset.filter(experiment__id=experiment_id)

        queryset = queryset.select_related("experiment", "participant__user", "chat")
        return queryset.annotate_with_message_count().order_by(F("last_activity_at").desc(nulls_last=True))
```

The `.prefetch_related(Prefetch("chat__tagged_items", ...))` call is removed; callers that need tag chips on the rendered page apply the prefetch themselves on the page slice.

- [ ] **Step 4: Add page-bounded prefetch to `ChatbotSessionsTableView`**

In `apps/chatbots/views.py`, update `ChatbotSessionsTableView`:

```python
class ChatbotSessionsTableView(LoginAndTeamRequiredMixin, PermissionRequiredMixin, SingleTableView):
    """View for rendering chatbot sessions table with filtering support."""

    model = ExperimentSession
    table_class = ChatbotSessionsTable
    template_name = "table/single_table.html"
    permission_required = "experiments.view_experimentsession"

    def get_queryset(self):
        experiment_id = self.kwargs.get("experiment_id")
        query_set = ExperimentSession.objects.get_table_queryset(self.request.team, experiment_id)
        timezone = self.request.session.get("detected_tz", None)
        session_filter = ExperimentSessionFilter()
        return session_filter.apply(
            query_set, filter_params=FilterParams.from_request(self.request), timezone=timezone
        )

    def get_table_data(self):
        """Materialise the page-sized slice, then attach the tag prefetch only to those rows."""
        from django.contrib.contenttypes.models import ContentType  # noqa: PLC0415
        from apps.annotations.models import CustomTaggedItem  # noqa: PLC0415
        from apps.chat.models import Chat  # noqa: PLC0415

        rows = list(super().get_table_data())
        if not rows:
            return rows
        chat_ids = [row.chat_id for row in rows]
        tagged_by_chat = {chat_id: [] for chat_id in chat_ids}
        chat_ct = ContentType.objects.get_for_model(Chat)
        for item in CustomTaggedItem.objects.filter(
            content_type=chat_ct, object_id__in=chat_ids,
        ).select_related("tag", "user"):
            tagged_by_chat[item.object_id].append(item)
        for row in rows:
            row.chat.prefetched_tagged_items = tagged_by_chat.get(row.chat_id, [])
        return rows

    def get_table(self, **kwargs):
        """When viewing sessions for a specific chatbot, hide the chatbot column."""
        table = super().get_table(**kwargs)
        if self.kwargs.get("experiment_id"):
            table.exclude = ("chatbot",)
        return table
```

- [ ] **Step 5: Apply the same shape to `AnnotationQueueSessionsTableView`**

In `apps/human_annotations/views/queue_views.py`, update `AnnotationQueueSessionsTableView.get_queryset` and add a `get_table_data`:

```python
class AnnotationQueueSessionsTableView(LoginAndTeamRequiredMixin, PermissionRequiredMixin, SingleTableView):
    """Filterable, paginated session table for selecting sessions to add to a queue."""

    model = ExperimentSession
    table_class = AnnotationSessionsSelectionTable
    template_name = "table/single_table_lazy_pagination.html"
    permission_required = "human_annotations.add_annotationitem"
    paginator_class = LazyPaginator

    def get_queryset(self):
        get_object_or_404(AnnotationQueue, id=self.kwargs["pk"], team=self.request.team)
        queryset = _get_available_sessions_queryset(self.request, self.kwargs["pk"])
        message_count_sq = (
            ChatMessage.objects.filter(chat=OuterRef("chat")).values("chat").annotate(c=Count("id")).values("c")
        )
        return (
            queryset.annotate(message_count=Coalesce(Subquery(message_count_sq), 0))
            .select_related("team", "participant__user", "chat", "experiment")
            .order_by("-last_activity_at")
        )

    def get_table_data(self):
        """Page-bounded tag prefetch — see ChatbotSessionsTableView for rationale."""
        from django.contrib.contenttypes.models import ContentType  # noqa: PLC0415
        from apps.annotations.models import CustomTaggedItem  # noqa: PLC0415
        from apps.chat.models import Chat  # noqa: PLC0415

        rows = list(super().get_table_data())
        if not rows:
            return rows
        chat_ids = [row.chat_id for row in rows]
        tagged_by_chat = {chat_id: [] for chat_id in chat_ids}
        chat_ct = ContentType.objects.get_for_model(Chat)
        for item in CustomTaggedItem.objects.filter(
            content_type=chat_ct, object_id__in=chat_ids,
        ).select_related("tag", "user"):
            tagged_by_chat[item.object_id].append(item)
        for row in rows:
            row.chat.prefetched_tagged_items = tagged_by_chat.get(row.chat_id, [])
        return rows
```

- [ ] **Step 6: Run the page-bounded test and the existing session view tests**

Run: `uv run pytest apps/chatbots/tests/test_chatbot_views.py::test_session_table_prefetch_is_page_bounded -v`

Expected: PASS.

Run: `uv run pytest apps/chatbots/tests/test_chatbot_views.py apps/human_annotations/tests/ -v`

Expected: PASS.

- [ ] **Step 7: Lint, type-check, commit**

Run: `uv run ruff check apps/experiments/models.py apps/chatbots/views.py apps/human_annotations/views/queue_views.py apps/chatbots/tests/test_chatbot_views.py --fix`
Run: `uv run ruff format apps/experiments/models.py apps/chatbots/views.py apps/human_annotations/views/queue_views.py apps/chatbots/tests/test_chatbot_views.py`
Run: `uv run ty check apps/experiments/ apps/chatbots/ apps/human_annotations/`

```bash
git add apps/experiments/models.py apps/chatbots/views.py apps/human_annotations/views/queue_views.py apps/chatbots/tests/test_chatbot_views.py
git commit -m "perf(sessions): bound CustomTaggedItem prefetch to visible page"
```

---

## Task 11: Investigate and remove the stray `experiment_channel` LEFT JOIN

`pg_stat_statements` shows the session-list `COUNT(*)` running with `LEFT OUTER JOIN channels_experimentchannel` even when no filter or display path needs it. We hunt down the source and remove it.

**Files (one of):**
- `apps/experiments/models.py:1395-1407` (`get_table_queryset` — an unintended `select_related` / `order_by` on `experiment_channel`)
- `apps/chatbots/tables.py` (a column accessor traversing `experiment_channel`)
- `apps/experiments/filters.py` (`ChannelsFilter` or a sibling using `experiment_channel__platform`)
- Test: `apps/experiments/tests/test_filters.py` (append)

Step 1 below identifies which one. Step 4 has a concrete fix recipe per candidate.

- [ ] **Step 1: Reproduce the JOIN locally**

Run a Django shell that builds the same queryset the view uses, in the same order:

```bash
uv run python manage.py shell -c "
from django.db import connection
from apps.chatbots.views import ChatbotSessionsTableView
from apps.experiments.filters import ExperimentSessionFilter
from apps.experiments.models import ExperimentSession
from apps.web.dynamic_filters.datastructures import FilterParams
from apps.teams.models import Team

team = Team.objects.first()
qs = ExperimentSession.objects.get_table_queryset(team)
qs = ExperimentSessionFilter().apply(qs, FilterParams())
print(qs.query)
print('---')
print(qs.values('id').query)  # mimics .count() shape
"
```

Expected: SQL output. Search it for `experiment_channel`. The match identifies which clause inserts the join.

- [ ] **Step 2: Write a regression test**

Append to `apps/experiments/tests/test_filters.py`:

```python
def test_session_filter_default_query_does_not_join_experiment_channel():
    """No filter, no display column references experiment_channel — confirm the COUNT
    does not pull it in unnecessarily."""
    from apps.experiments.models import ExperimentSession  # noqa: PLC0415
    from apps.teams.models import Team  # noqa: PLC0415

    team = Team.objects.first() or TeamFactory.create()
    qs = ExperimentSession.objects.get_table_queryset(team)
    qs = ExperimentSessionFilter().apply(qs, FilterParams())
    sql = str(qs.values("id").query).lower()
    assert "channels_experimentchannel" not in sql, sql
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest apps/experiments/tests/test_filters.py::test_session_filter_default_query_does_not_join_experiment_channel -v`

Expected: FAIL.

- [ ] **Step 4: Apply the fix identified in Step 1**

The fix depends on what Step 1 finds — the most likely candidates and what to do about each:

- **If `select_related` includes `experiment_channel`:** remove it from the `select_related` chain in `apps/experiments/models.py:1400`.
- **If a column accessor in `ChatbotSessionsTable` traverses `experiment_channel`:** narrow the accessor or render via a callable that uses the existing FK-id only.
- **If `ChannelsFilter` is somehow joining via `experiment_channel__platform`:** confirm the filter is using `column = "platform"` (the column on `ExperimentSession` itself, set on session save). If a stray `experiment_channel__` reference is found, change it to `platform`.
- **If the table's `Meta.fields` references a deferred attribute that triggers the join:** remove or rename the field.

Whichever it is, make the smallest possible change.

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest apps/experiments/tests/test_filters.py::test_session_filter_default_query_does_not_join_experiment_channel -v`

Expected: PASS.

Run the full session-related test suite as a regression check:

Run: `uv run pytest apps/chatbots/tests/test_chatbot_views.py apps/experiments/tests/test_filters.py apps/human_annotations/tests/ -v`

Expected: PASS.

- [ ] **Step 6: Lint, type-check, commit**

Run: `uv run ruff check <file_modified> apps/experiments/tests/test_filters.py --fix`
Run: `uv run ruff format <file_modified> apps/experiments/tests/test_filters.py`
Run: `uv run ty check apps/experiments/ apps/chatbots/`

```bash
git add <file_modified> apps/experiments/tests/test_filters.py
git commit -m "perf(sessions): drop stray experiment_channel join from default queryset"
```

(The committer fills in `<file_modified>` based on Step 1. The commit message is intentionally specific — if the fix turns out to be elsewhere than the queryset, edit accordingly.)

---

## Task 12: Audit `Experiment.objects.get_all()` for the chatbot list `SELECT DISTINCT`

Final low-effort audit: the chatbot-list query emits `SELECT DISTINCT experiment` per pg_stat. Confirm whether the `DISTINCT` is needed and remove it if not.

**Files:**
- Read: `apps/experiments/models.py` (look at the `Experiment` manager / `get_all` / version-related querysets)
- Modify: depends on findings

- [ ] **Step 1: Find the source of `DISTINCT`**

Run: `uv run python manage.py shell -c "
from apps.experiments.models import Experiment
from apps.teams.models import Team

team = Team.objects.first()
qs = (Experiment.objects.get_all()
      .filter(team=team, working_version__isnull=True, pipeline__isnull=False)
      .filter(is_archived=False))
print(qs.query)
"`

Search the SQL for `DISTINCT`. If absent, the DISTINCT we saw in pg_stat originates from an annotation, similarity_search, or a specific filter path — note where, and exit Step 1 documenting the finding.

- [ ] **Step 2: Make the appropriate decision**

- **If the DISTINCT is from a join the view does not need** (e.g. the manager joins through versions and dedupes), narrow the manager method or filter the queryset to avoid the join. Add a SQL-shape test asserting `DISTINCT` is absent from the chatbot-list queryset.
- **If the DISTINCT is needed for correctness** (e.g. it dedupes after a real one-to-many traversal that we cannot avoid without a behaviour change), document this in a one-line comment at the source and close the task with no code change. Add a comment in `apps/chatbots/views.py` referencing this finding so future readers don't repeat the audit.

- [ ] **Step 3: If a code change was made, write a regression test**

Append to `apps/chatbots/tests/test_chatbot_views.py` (only if Step 2 produced a code change):

```python
@pytest.mark.django_db()
def test_chatbot_list_queryset_has_no_select_distinct(team_with_users):
    team = team_with_users
    user = team.members.first()
    factory = RequestFactory()
    request = factory.get(reverse("chatbots:table", args=[team.slug]))
    request.user = user
    request.team = team
    request.team_membership = get_team_membership_for_request(request)
    attach_session_middleware_to_request(request)
    set_current_team(team)

    view = ChatbotExperimentTableView()
    view.request = request
    view.kwargs = {"team_slug": team.slug}
    sql = str(view.get_queryset().query).lower()
    assert "distinct" not in sql, sql
```

- [ ] **Step 4: Run tests, lint, type-check, commit**

Run: `uv run pytest apps/chatbots/tests/test_chatbot_views.py -v`

Expected: PASS.

Run: `uv run ruff check <files_modified> --fix`
Run: `uv run ruff format <files_modified>`
Run: `uv run ty check apps/chatbots/ apps/experiments/`

```bash
git add <files_modified>
git commit -m "perf(chatbots): drop unused DISTINCT from list queryset"   # or: "docs(chatbots): note why list queryset retains DISTINCT"
```

---

## Measurement

After each task, append a row to `docs/superpowers/specs/2026-04-30-list-view-performance-results.md` with the format below. Create the file before the first measurement.

```markdown
## Task <N> — <short title>

**Date:** YYYY-MM-DD
**pg_stat_statements deltas (mean / total / calls):**
- `SELECT trace_trace …`: before X / Y / Z, after X' / Y' / Z'
- `COUNT(*) … DISTINCT experimentsession …`: before / after
- `customtaggeditem …`: before / after

**Sentry transaction p95 deltas:**
- `chatbots:table`: before / after
- `chatbots:sessions-list`: before / after
- `trace:table`: before / after
- `human_annotations:queue_sessions_table`: before / after

**Notes:** any surprises; whether the change moved the right numbers.
```

After Task 9 ships, do a full re-snapshot of `pg_stat_statements` for the four tracked queries and compare against the success criteria in the spec:

- Trace list main query mean execution time drops by ≥80%
- Session list `COUNT(*) … DISTINCT` total time drops by ≥50%
- No new duplicate-row regressions in test or staging QA
- Trace / ExperimentSession insert latency unchanged within ±10%

If gates pass, Approach 1 is complete. Otherwise, re-investigate before continuing to Tasks 10–12, and capture the residual hotspots — they feed into Approach 2.

---

## Self-review notes

Read this section last; it is for the implementer to confirm the plan still makes sense before starting.

- **Spec coverage:** every "in scope" bullet from the spec maps to at least one task. Trace indexes → Task 1; team__slug → Task 2; ExperimentSession index → Task 3; DISTINCT removal + EXISTS → Tasks 4–9; CustomTaggedItem prefetch → Task 10; experiment_channel JOIN → Task 11; chatbot list DISTINCT audit → Task 12.
- **Test order:** Tasks 4 and 7 land regression tests that pass under current code, so they cannot prove the rewrites. The proof comes in Task 9, where the global `.distinct()` is removed and the same regression tests must still pass. Each task is still independently shippable because EXISTS is behaviour-preserving on its own.
- **Local imports:** Tasks 6, 8, 10, 11, 12 each include local imports inside method bodies. Per `AGENTS.md`, top-level imports are the project default; the local form is used only to avoid a circular import or, for `MessageTimestampFilter._exists`, to keep `apps.web.dynamic_filters` from importing `apps.chat` at module load.
- **Migration numbers:** `apps/trace/migrations/0012_…` follows `0011_add_trace_metrics.py`; `apps/experiments/migrations/0134_…` follows `0133_alter_syntheticvoice_service.py`. Confirm at the start of Tasks 1 and 3 in case of merges in the meantime.
