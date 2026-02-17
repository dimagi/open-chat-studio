# Human Annotation/Labeling System - Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a human annotation system that lets teams create annotation queues with customizable schemas, assign reviewers, and collect structured feedback on chat sessions/messages.

**Architecture:** New Django app `human_annotations` (since `annotations` is already taken by the tagging system) with models for queues, items, and annotations. The annotation schema (field definitions) is stored as a JSON field directly on `AnnotationQueue`, reusing the `FieldDefinition` pattern from evaluations. Team-scoped via `BaseTeamModel`. HTMX-powered UI following existing patterns. Gated behind a Waffle feature flag.

**Tech Stack:** Django 5.x, PostgreSQL, HTMX, Alpine.js, django-tables2, DaisyUI/TailwindCSS

**Scope:** This plan covers Phase 1 (core features: items 1-6 from the ticket). Phase 2+ features (quality control, evaluation integration, session UI integration, automation, notifications) are out of scope and will be planned separately after Phase 1 ships.

**GitHub Issue:** https://github.com/dimagi/open-chat-studio/issues/2682

---

## Changelog

- **2026-02-17:** Merged `AnnotationSchema` model into `AnnotationQueue`. The schema JSON field and `get_field_definitions()` now live directly on the queue. Removed all schema CRUD views, URLs, templates, and nav entries. The queue create/edit form includes an inline Alpine.js schema field builder. Also removed CSV import feature and `external_data` field from `AnnotationItem`.

---

## Phase 1 Overview

Phase 1 delivers:
- **Annotation Queues** - assignable queues with inline schema, configurable N-reviews-per-item
- **Annotation Items** - items linked to sessions or messages
- **Annotations** - submitted reviews with validated data
- **Admin Dashboard** - create/manage queues, bulk add items, view progress, export
- **Annotator UI** - focused one-at-a-time annotation interface

---

## Task 1: Create the Django App Skeleton

**Files:**
- Create: `apps/human_annotations/__init__.py`
- Create: `apps/human_annotations/apps.py`
- Create: `apps/human_annotations/models.py`
- Create: `apps/human_annotations/admin.py`
- Create: `apps/human_annotations/urls.py`
- Create: `apps/human_annotations/views/__init__.py`
- Create: `apps/human_annotations/forms.py`
- Create: `apps/human_annotations/tables.py`
- Create: `apps/human_annotations/tasks.py`
- Create: `apps/human_annotations/tests/__init__.py`
- Modify: `config/settings.py` (add to PROJECT_APPS)
- Modify: `config/urls.py` (add to team_urlpatterns)

**Step 1: Create the app directory and files**

```python
# apps/human_annotations/__init__.py
# (empty)
```

```python
# apps/human_annotations/apps.py
from django.apps import AppConfig


class HumanAnnotationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.human_annotations"
    label = "human_annotations"
```

```python
# apps/human_annotations/models.py
# Models will be added in subsequent tasks
```

```python
# apps/human_annotations/admin.py
from django.contrib import admin  # noqa: F401
# Admin registrations will be added with models
```

```python
# apps/human_annotations/urls.py
from django.urls import path  # noqa: F401

app_name = "human_annotations"

urlpatterns = []
```

```python
# apps/human_annotations/views/__init__.py
# Views will be added in subsequent tasks
```

```python
# apps/human_annotations/forms.py
# Forms will be added in subsequent tasks
```

```python
# apps/human_annotations/tables.py
# Tables will be added in subsequent tasks
```

```python
# apps/human_annotations/tasks.py
# Celery tasks will be added in subsequent tasks
```

```python
# apps/human_annotations/tests/__init__.py
# (empty)
```

**Step 2: Register the app in settings**

In `config/settings.py`, add `"apps.human_annotations"` to the `PROJECT_APPS` list (after `"apps.evaluations"`).

**Step 3: Register URLs**

In `config/urls.py`, add to `team_urlpatterns`:
```python
path("human-annotations/", include("apps.human_annotations.urls")),
```

**Step 4: Create and run initial migration**

Run: `python manage.py makemigrations human_annotations`
Expected: Empty initial migration created

Run: `python manage.py migrate`
Expected: Migration applied successfully

**Step 5: Commit**

```bash
git add apps/human_annotations/ config/settings.py config/urls.py
git commit -m "feat: scaffold human_annotations Django app"
```

---

## Task 2: AnnotationSchema Model

**Files:**
- Modify: `apps/human_annotations/models.py`
- Create: `apps/human_annotations/tests/test_models.py`
- Modify: `apps/human_annotations/admin.py`
- Create: `apps/utils/factories/human_annotations.py`

**Context:** An `AnnotationSchema` defines what data annotators will collect. It reuses the `FieldDefinition` union type from `apps/evaluations/field_definitions.py`. Each schema has a name and a JSON field storing a dict of field name -> FieldDefinition. Schemas are team-scoped and reusable across queues.

**Step 1: Write the failing test**

```python
# apps/human_annotations/tests/test_models.py
import pytest

from apps.human_annotations.models import AnnotationSchema
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def team():
    return TeamWithUsersFactory()


@pytest.mark.django_db()
def test_create_annotation_schema(team):
    schema = AnnotationSchema.objects.create(
        team=team,
        name="Quality Review",
        schema={
            "quality_score": {"type": "int", "description": "Overall quality 1-5", "ge": 1, "le": 5},
            "category": {
                "type": "choice",
                "description": "Response category",
                "choices": ["correct", "partially_correct", "incorrect"],
            },
            "notes": {"type": "string", "description": "Additional notes"},
        },
    )
    assert schema.id is not None
    assert schema.name == "Quality Review"
    assert len(schema.schema) == 3
    assert schema.schema["quality_score"]["type"] == "int"


@pytest.mark.django_db()
def test_annotation_schema_unique_name_per_team(team):
    AnnotationSchema.objects.create(team=team, name="Test Schema", schema={})
    with pytest.raises(Exception):
        AnnotationSchema.objects.create(team=team, name="Test Schema", schema={})


@pytest.mark.django_db()
def test_annotation_schema_get_field_definitions(team):
    schema = AnnotationSchema.objects.create(
        team=team,
        name="Test",
        schema={
            "score": {"type": "int", "description": "Score", "ge": 1, "le": 5},
        },
    )
    field_defs = schema.get_field_definitions()
    assert "score" in field_defs
    assert field_defs["score"].type == "int"
    assert field_defs["score"].ge == 1
    assert field_defs["score"].le == 5
```

**Step 2: Run test to verify it fails**

Run: `pytest apps/human_annotations/tests/test_models.py -v`
Expected: FAIL - `AnnotationSchema` not defined

**Step 3: Write the model**

```python
# apps/human_annotations/models.py
from django.db import models
from pydantic import TypeAdapter

from apps.evaluations.field_definitions import FieldDefinition
from apps.teams.models import BaseTeamModel
from apps.utils.fields import SanitizedJSONField


class AnnotationSchema(BaseTeamModel):
    """Defines the fields annotators will fill out. Reuses FieldDefinition from evaluations."""

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    schema = SanitizedJSONField(
        default=dict,
        help_text="Dict of field_name -> FieldDefinition JSON (same format as evaluator output_schema)",
    )

    class Meta:
        unique_together = ("team", "name")
        ordering = ["name"]

    def __str__(self):
        return self.name

    def get_field_definitions(self) -> dict[str, FieldDefinition]:
        """Parse the raw JSON schema into typed FieldDefinition objects."""
        adapter = TypeAdapter(FieldDefinition)
        return {name: adapter.validate_python(defn) for name, defn in self.schema.items()}
```

**Step 4: Register admin**

```python
# apps/human_annotations/admin.py
from django.contrib import admin

from .models import AnnotationSchema


@admin.register(AnnotationSchema)
class AnnotationSchemaAdmin(admin.ModelAdmin):
    list_display = ("name", "team", "created_at")
    list_filter = ("team",)
    search_fields = ("name",)
```

**Step 5: Create factory**

```python
# apps/utils/factories/human_annotations.py
import factory

from apps.human_annotations.models import AnnotationSchema
from apps.utils.factories.team import TeamFactory


class AnnotationSchemaFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = AnnotationSchema

    team = factory.SubFactory(TeamFactory)
    name = factory.Sequence(lambda n: f"Schema {n}")
    schema = factory.LazyFunction(
        lambda: {
            "quality_score": {"type": "int", "description": "Overall quality 1-5", "ge": 1, "le": 5},
            "notes": {"type": "string", "description": "Additional notes"},
        }
    )
```

**Step 6: Make migration and run tests**

Run: `python manage.py makemigrations human_annotations`
Run: `pytest apps/human_annotations/tests/test_models.py -v`
Expected: All 3 tests PASS

**Step 7: Lint**

Run: `ruff check apps/human_annotations/ --fix && ruff format apps/human_annotations/`
Run: `ruff check apps/utils/factories/human_annotations.py --fix && ruff format apps/utils/factories/human_annotations.py`

**Step 8: Commit**

```bash
git add apps/human_annotations/ apps/utils/factories/human_annotations.py
git commit -m "feat: add AnnotationSchema model with FieldDefinition reuse"
```

---

## Task 3: AnnotationQueue Model

**Files:**
- Modify: `apps/human_annotations/models.py`
- Modify: `apps/human_annotations/tests/test_models.py`
- Modify: `apps/human_annotations/admin.py`
- Modify: `apps/utils/factories/human_annotations.py`

**Context:** An `AnnotationQueue` groups items for annotation. It has a schema, assigned reviewers (M2M to User), a configurable `num_reviews_required` (1-10), and status tracking. Queue owner is the creator.

**Step 1: Write the failing tests**

Append to `apps/human_annotations/tests/test_models.py`:

```python
from apps.human_annotations.models import AnnotationQueue, QueueStatus


@pytest.mark.django_db()
def test_create_annotation_queue(team):
    schema = AnnotationSchema.objects.create(team=team, name="Test", schema={})
    user = team.members.first()
    queue = AnnotationQueue.objects.create(
        team=team,
        name="Quality Audit Q1",
        schema=schema,
        created_by=user,
        num_reviews_required=3,
    )
    queue.assignees.add(user)
    assert queue.id is not None
    assert queue.status == QueueStatus.ACTIVE
    assert queue.num_reviews_required == 3
    assert queue.assignees.count() == 1


@pytest.mark.django_db()
def test_queue_progress_empty(team):
    schema = AnnotationSchema.objects.create(team=team, name="Test", schema={})
    user = team.members.first()
    queue = AnnotationQueue.objects.create(
        team=team, name="Empty Queue", schema=schema, created_by=user,
    )
    progress = queue.get_progress()
    assert progress["total"] == 0
    assert progress["completed"] == 0
    assert progress["percent"] == 0
```

**Step 2: Run test to verify it fails**

Run: `pytest apps/human_annotations/tests/test_models.py::test_create_annotation_queue -v`
Expected: FAIL - `AnnotationQueue` not defined

**Step 3: Write the model**

Add to `apps/human_annotations/models.py`:

```python
from django.conf import settings


class QueueStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    PAUSED = "paused", "Paused"
    COMPLETED = "completed", "Completed"
    ARCHIVED = "archived", "Archived"


class AnnotationQueue(BaseTeamModel):
    """A queue of items to be annotated by assigned reviewers."""

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    schema = models.ForeignKey(AnnotationSchema, on_delete=models.PROTECT, related_name="queues")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="created_annotation_queues",
    )
    assignees = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="assigned_annotation_queues")
    num_reviews_required = models.PositiveSmallIntegerField(
        default=1,
        help_text="Number of reviews required before an item is marked complete (1-10)",
    )
    status = models.CharField(max_length=20, choices=QueueStatus.choices, default=QueueStatus.ACTIVE)

    class Meta:
        unique_together = ("team", "name")
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    def get_progress(self):
        """Return progress stats: total items, completed, percent."""
        total = self.items.count()
        completed = self.items.filter(status=AnnotationItemStatus.COMPLETED).count()
        percent = round((completed / total) * 100) if total > 0 else 0
        return {"total": total, "completed": completed, "percent": percent}
```

Note: `AnnotationItemStatus` is defined in Task 4. For the migration to succeed, we need to add a forward reference or define the status choices inline. We'll use a string reference for now and the `get_progress` method will work once `AnnotationItem` exists. For the initial migration, comment out `get_progress` or define `AnnotationItemStatus` early. Practically, define `AnnotationItemStatus` at the top of models.py before `AnnotationQueue`:

```python
class AnnotationItemStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    IN_PROGRESS = "in_progress", "In Progress"
    COMPLETED = "completed", "Completed"
    FLAGGED = "flagged", "Flagged"
```

**Step 4: Update admin and factory**

Admin:
```python
@admin.register(AnnotationQueue)
class AnnotationQueueAdmin(admin.ModelAdmin):
    list_display = ("name", "team", "schema", "status", "num_reviews_required", "created_at")
    list_filter = ("team", "status")
    search_fields = ("name",)
```

Factory:
```python
class AnnotationQueueFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "human_annotations.AnnotationQueue"

    team = factory.SubFactory(TeamFactory)
    name = factory.Sequence(lambda n: f"Queue {n}")
    schema = factory.SubFactory(AnnotationSchemaFactory, team=factory.SelfAttribute("..team"))
    created_by = factory.LazyAttribute(lambda obj: obj.team.members.first())
    num_reviews_required = 1
```

**Step 5: Make migration and run tests**

Run: `python manage.py makemigrations human_annotations`
Run: `pytest apps/human_annotations/tests/test_models.py -v`
Expected: All tests PASS

**Step 6: Lint and commit**

Run: `ruff check apps/human_annotations/ apps/utils/factories/human_annotations.py --fix && ruff format apps/human_annotations/ apps/utils/factories/human_annotations.py`

```bash
git add apps/human_annotations/ apps/utils/factories/human_annotations.py
git commit -m "feat: add AnnotationQueue model with assignees and review requirements"
```

---

## Task 4: AnnotationItem Model

**Files:**
- Modify: `apps/human_annotations/models.py`
- Modify: `apps/human_annotations/tests/test_models.py`
- Modify: `apps/human_annotations/admin.py`
- Modify: `apps/utils/factories/human_annotations.py`

**Context:** An `AnnotationItem` represents a single thing to annotate. It can be linked to an `ExperimentSession` (full session), a `ChatMessage` (single message), or external data (stored as JSON from CSV import). Items track their status and review count.

**Step 1: Write the failing tests**

Append to `apps/human_annotations/tests/test_models.py`:

```python
from apps.human_annotations.models import AnnotationItem, AnnotationItemStatus, AnnotationItemType
from apps.utils.factories.experiment import ExperimentSessionFactory


@pytest.mark.django_db()
def test_create_item_from_session(team):
    schema = AnnotationSchema.objects.create(team=team, name="Test", schema={})
    user = team.members.first()
    queue = AnnotationQueue.objects.create(team=team, name="Q", schema=schema, created_by=user)
    session = ExperimentSessionFactory(team=team, chat__team=team)

    item = AnnotationItem.objects.create(
        queue=queue,
        team=team,
        item_type=AnnotationItemType.SESSION,
        session=session,
    )
    assert item.status == AnnotationItemStatus.PENDING
    assert item.session == session
    assert item.review_count == 0


@pytest.mark.django_db()
def test_create_item_from_external_data(team):
    schema = AnnotationSchema.objects.create(team=team, name="Test", schema={})
    user = team.members.first()
    queue = AnnotationQueue.objects.create(team=team, name="Q", schema=schema, created_by=user)

    item = AnnotationItem.objects.create(
        queue=queue,
        team=team,
        item_type=AnnotationItemType.EXTERNAL,
        external_data={"input": "Hello", "output": "Hi there!", "context": "greeting"},
    )
    assert item.item_type == AnnotationItemType.EXTERNAL
    assert item.external_data["input"] == "Hello"


@pytest.mark.django_db()
def test_item_prevents_duplicate_session_in_queue(team):
    schema = AnnotationSchema.objects.create(team=team, name="Test", schema={})
    user = team.members.first()
    queue = AnnotationQueue.objects.create(team=team, name="Q", schema=schema, created_by=user)
    session = ExperimentSessionFactory(team=team, chat__team=team)

    AnnotationItem.objects.create(
        queue=queue, team=team, item_type=AnnotationItemType.SESSION, session=session,
    )
    with pytest.raises(Exception):
        AnnotationItem.objects.create(
            queue=queue, team=team, item_type=AnnotationItemType.SESSION, session=session,
        )
```

**Step 2: Run test to verify it fails**

Run: `pytest apps/human_annotations/tests/test_models.py::test_create_item_from_session -v`
Expected: FAIL

**Step 3: Write the model**

Add to `apps/human_annotations/models.py` (note `AnnotationItemStatus` was already defined in Task 3):

```python
from apps.chat.models import ChatMessage
from apps.experiments.models import ExperimentSession


class AnnotationItemType(models.TextChoices):
    SESSION = "session", "Session"
    MESSAGE = "message", "Message"
    EXTERNAL = "external", "External Data"


class AnnotationItem(BaseTeamModel):
    """A single item in an annotation queue to be reviewed."""

    queue = models.ForeignKey(AnnotationQueue, on_delete=models.CASCADE, related_name="items")
    item_type = models.CharField(max_length=20, choices=AnnotationItemType.choices)
    status = models.CharField(
        max_length=20, choices=AnnotationItemStatus.choices, default=AnnotationItemStatus.PENDING,
    )

    # Linked objects (nullable depending on item_type)
    session = models.ForeignKey(
        ExperimentSession, on_delete=models.CASCADE, null=True, blank=True, related_name="annotation_items",
    )
    message = models.ForeignKey(
        ChatMessage, on_delete=models.CASCADE, null=True, blank=True, related_name="annotation_items",
    )

    # For external/CSV data
    external_data = SanitizedJSONField(default=dict, blank=True)

    # Denormalized review count for efficient querying
    review_count = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["queue", "status"]),
            models.Index(fields=["queue", "created_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["queue", "session"],
                condition=models.Q(session__isnull=False),
                name="unique_session_per_queue",
            ),
            models.UniqueConstraint(
                fields=["queue", "message"],
                condition=models.Q(message__isnull=False),
                name="unique_message_per_queue",
            ),
        ]

    def __str__(self):
        if self.session_id:
            return f"Session {self.session.external_id}"
        if self.message_id:
            return f"Message {self.message_id}"
        return f"External item {self.id}"

    def update_status(self):
        """Update item status based on review count vs queue requirement."""
        if self.review_count >= self.queue.num_reviews_required:
            self.status = AnnotationItemStatus.COMPLETED
        elif self.review_count > 0:
            self.status = AnnotationItemStatus.IN_PROGRESS
        self.save(update_fields=["status"])
```

**Step 4: Update admin and factory**

Admin:
```python
@admin.register(AnnotationItem)
class AnnotationItemAdmin(admin.ModelAdmin):
    list_display = ("id", "queue", "item_type", "status", "review_count", "created_at")
    list_filter = ("status", "item_type")
```

Factory:
```python
from apps.utils.factories.experiment import ExperimentSessionFactory

class AnnotationItemFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "human_annotations.AnnotationItem"

    queue = factory.SubFactory(AnnotationQueueFactory)
    team = factory.SelfAttribute("queue.team")
    item_type = "session"
    session = factory.SubFactory(
        ExperimentSessionFactory,
        team=factory.SelfAttribute("..team"),
        chat__team=factory.SelfAttribute("..team"),
    )
```

**Step 5: Make migration and run tests**

Run: `python manage.py makemigrations human_annotations`
Run: `pytest apps/human_annotations/tests/test_models.py -v`
Expected: All tests PASS

**Step 6: Lint and commit**

Run: `ruff check apps/human_annotations/ apps/utils/factories/human_annotations.py --fix && ruff format apps/human_annotations/ apps/utils/factories/human_annotations.py`

```bash
git add apps/human_annotations/ apps/utils/factories/human_annotations.py
git commit -m "feat: add AnnotationItem model with session/message/external support"
```

---

## Task 5: Annotation Model (Review Submission)

**Files:**
- Modify: `apps/human_annotations/models.py`
- Modify: `apps/human_annotations/tests/test_models.py`
- Modify: `apps/human_annotations/admin.py`
- Modify: `apps/utils/factories/human_annotations.py`

**Context:** An `Annotation` is a submitted review. It stores the reviewer, the item, and the annotation data (validated against the queue's schema). The system prevents duplicate annotations (same user + same item) and updates the item's review count on save.

**Step 1: Write the failing tests**

Append to `apps/human_annotations/tests/test_models.py`:

```python
from apps.human_annotations.models import Annotation


@pytest.mark.django_db()
def test_create_annotation(team):
    user = team.members.first()
    schema = AnnotationSchema.objects.create(
        team=team,
        name="Test",
        schema={"score": {"type": "int", "description": "Score", "ge": 1, "le": 5}},
    )
    queue = AnnotationQueue.objects.create(
        team=team, name="Q", schema=schema, created_by=user, num_reviews_required=2,
    )
    session = ExperimentSessionFactory(team=team, chat__team=team)
    item = AnnotationItem.objects.create(
        queue=queue, team=team, item_type=AnnotationItemType.SESSION, session=session,
    )

    annotation = Annotation.objects.create(
        item=item, team=team, reviewer=user, data={"score": 4},
    )
    assert annotation.id is not None
    item.refresh_from_db()
    assert item.review_count == 1
    assert item.status == AnnotationItemStatus.IN_PROGRESS


@pytest.mark.django_db()
def test_annotation_completes_item_when_reviews_met(team):
    user1 = team.members.first()
    user2 = team.members.last()
    schema = AnnotationSchema.objects.create(
        team=team,
        name="Test",
        schema={"score": {"type": "int", "description": "Score", "ge": 1, "le": 5}},
    )
    queue = AnnotationQueue.objects.create(
        team=team, name="Q", schema=schema, created_by=user1, num_reviews_required=2,
    )
    session = ExperimentSessionFactory(team=team, chat__team=team)
    item = AnnotationItem.objects.create(
        queue=queue, team=team, item_type=AnnotationItemType.SESSION, session=session,
    )

    Annotation.objects.create(item=item, team=team, reviewer=user1, data={"score": 4})
    Annotation.objects.create(item=item, team=team, reviewer=user2, data={"score": 3})
    item.refresh_from_db()
    assert item.review_count == 2
    assert item.status == AnnotationItemStatus.COMPLETED


@pytest.mark.django_db()
def test_annotation_prevents_duplicate_reviewer(team):
    user = team.members.first()
    schema = AnnotationSchema.objects.create(team=team, name="Test", schema={})
    queue = AnnotationQueue.objects.create(team=team, name="Q", schema=schema, created_by=user)
    session = ExperimentSessionFactory(team=team, chat__team=team)
    item = AnnotationItem.objects.create(
        queue=queue, team=team, item_type=AnnotationItemType.SESSION, session=session,
    )

    Annotation.objects.create(item=item, team=team, reviewer=user, data={})
    with pytest.raises(Exception):
        Annotation.objects.create(item=item, team=team, reviewer=user, data={})
```

**Step 2: Run test to verify it fails**

Run: `pytest apps/human_annotations/tests/test_models.py::test_create_annotation -v`
Expected: FAIL

**Step 3: Write the model**

Add to `apps/human_annotations/models.py`:

```python
class AnnotationStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted"


class Annotation(BaseTeamModel):
    """A single review/annotation submitted by a reviewer for an item."""

    item = models.ForeignKey(AnnotationItem, on_delete=models.CASCADE, related_name="annotations")
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="annotations",
    )
    data = SanitizedJSONField(default=dict, help_text="Annotation data matching the queue's schema")
    status = models.CharField(
        max_length=20, choices=AnnotationStatus.choices, default=AnnotationStatus.SUBMITTED,
    )

    class Meta:
        unique_together = ("item", "reviewer")
        ordering = ["-created_at"]

    def __str__(self):
        return f"Annotation by {self.reviewer} on item {self.item_id}"

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        super().save(*args, **kwargs)
        if is_new and self.status == AnnotationStatus.SUBMITTED:
            self._update_item_review_count()

    def _update_item_review_count(self):
        """Increment item review count and update status."""
        count = self.item.annotations.filter(status=AnnotationStatus.SUBMITTED).count()
        self.item.review_count = count
        self.item.save(update_fields=["review_count"])
        self.item.update_status()
```

**Step 4: Update admin and factory**

Admin:
```python
@admin.register(Annotation)
class AnnotationAdmin(admin.ModelAdmin):
    list_display = ("id", "item", "reviewer", "status", "created_at")
    list_filter = ("status",)
```

Factory:
```python
from apps.utils.factories.user import UserFactory

class AnnotationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "human_annotations.Annotation"

    item = factory.SubFactory(AnnotationItemFactory)
    team = factory.SelfAttribute("item.team")
    reviewer = factory.SubFactory(UserFactory)
    data = factory.LazyFunction(lambda: {"score": 3})
```

**Step 5: Make migration and run tests**

Run: `python manage.py makemigrations human_annotations`
Run: `pytest apps/human_annotations/tests/test_models.py -v`
Expected: All tests PASS

**Step 6: Lint and commit**

Run: `ruff check apps/human_annotations/ apps/utils/factories/human_annotations.py --fix && ruff format apps/human_annotations/ apps/utils/factories/human_annotations.py`

```bash
git add apps/human_annotations/ apps/utils/factories/human_annotations.py
git commit -m "feat: add Annotation model with duplicate prevention and auto-status"
```

---

## Task 6: Schema CRUD Views

**Files:**
- Modify: `apps/human_annotations/views/__init__.py`
- Create: `apps/human_annotations/views/schema_views.py`
- Modify: `apps/human_annotations/forms.py`
- Modify: `apps/human_annotations/tables.py`
- Modify: `apps/human_annotations/urls.py`
- Create: `apps/human_annotations/tests/test_views.py`

**Context:** Following the existing CRUD pattern (see `apps/annotations/views/tag_views.py`), create views for managing AnnotationSchemas: Home, Table, Create, Edit, Delete. Use the `make_crud_urls()` helper. Use generic templates (`generic/object_home.html`, `generic/object_form.html`).

**Step 1: Write the failing tests**

```python
# apps/human_annotations/tests/test_views.py
import pytest
from django.urls import reverse

from apps.human_annotations.models import AnnotationSchema
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def team():
    return TeamWithUsersFactory()


@pytest.fixture()
def logged_in_client(team, client):
    user = team.members.first()
    client.login(username=user.username, password="password")
    return client


@pytest.mark.django_db()
def test_schema_home(logged_in_client, team):
    url = reverse("human_annotations:schema_home", kwargs={"team_slug": team.slug})
    response = logged_in_client.get(url)
    assert response.status_code == 200


@pytest.mark.django_db()
def test_create_schema(logged_in_client, team):
    url = reverse("human_annotations:schema_new", kwargs={"team_slug": team.slug})
    response = logged_in_client.post(url, data={
        "name": "My Schema",
        "description": "Test schema",
        "schema": '{"score": {"type": "int", "description": "Score", "ge": 1, "le": 5}}',
    })
    assert response.status_code == 302
    assert AnnotationSchema.objects.filter(team=team, name="My Schema").exists()


@pytest.mark.django_db()
def test_schema_table(logged_in_client, team):
    AnnotationSchema.objects.create(team=team, name="S1", schema={})
    url = reverse("human_annotations:schema_table", kwargs={"team_slug": team.slug})
    response = logged_in_client.get(url)
    assert response.status_code == 200
```

**Step 2: Run test to verify it fails**

Run: `pytest apps/human_annotations/tests/test_views.py -v`
Expected: FAIL - URL not found

**Step 3: Write the form**

```python
# apps/human_annotations/forms.py
import json

from django import forms
from django.core.exceptions import ValidationError
from pydantic import TypeAdapter

from apps.evaluations.field_definitions import FieldDefinition

from .models import AnnotationSchema


class AnnotationSchemaForm(forms.ModelForm):
    schema = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 10, "class": "textarea textarea-bordered font-mono text-sm"}),
        help_text='JSON dict of field_name -> FieldDefinition. Example: {"score": {"type": "int", "description": "Score 1-5", "ge": 1, "le": 5}}',
    )

    class Meta:
        model = AnnotationSchema
        fields = ["name", "description", "schema"]

    def clean_schema(self):
        raw = self.cleaned_data["schema"]
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValidationError(f"Invalid JSON: {e}")

        if not isinstance(data, dict):
            raise ValidationError("Schema must be a JSON object (dict)")

        if not data:
            raise ValidationError("Schema must have at least one field")

        adapter = TypeAdapter(FieldDefinition)
        for name, defn in data.items():
            try:
                adapter.validate_python(defn)
            except Exception as e:
                raise ValidationError(f"Invalid field '{name}': {e}")

        return data
```

**Step 4: Write the table**

```python
# apps/human_annotations/tables.py
import django_tables2 as tables

from .models import AnnotationSchema


class AnnotationSchemaTable(tables.Table):
    name = tables.Column(linkify=True)
    field_count = tables.Column(verbose_name="Fields", empty_values=(), orderable=False)

    class Meta:
        model = AnnotationSchema
        fields = ["name", "description", "field_count", "created_at"]
        attrs = {"class": "table"}

    def render_field_count(self, record):
        return len(record.schema)
```

**Step 5: Write the views**

```python
# apps/human_annotations/views/schema_views.py
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, TemplateView, UpdateView
from django_tables2 import SingleTableView

from apps.teams.mixins import LoginAndTeamRequiredMixin

from ..forms import AnnotationSchemaForm
from ..models import AnnotationSchema
from ..tables import AnnotationSchemaTable


class AnnotationSchemaHome(LoginAndTeamRequiredMixin, TemplateView, PermissionRequiredMixin):
    template_name = "generic/object_home.html"
    permission_required = "human_annotations.view_annotationschema"

    def get_context_data(self, team_slug: str, **kwargs):
        return {
            "active_tab": "annotation_schemas",
            "title": "Annotation Schemas",
            "new_object_url": reverse("human_annotations:schema_new", args=[team_slug]),
            "table_url": reverse("human_annotations:schema_table", args=[team_slug]),
            "enable_search": True,
        }


class AnnotationSchemaTableView(LoginAndTeamRequiredMixin, SingleTableView, PermissionRequiredMixin):
    model = AnnotationSchema
    table_class = AnnotationSchemaTable
    template_name = "table/single_table.html"
    permission_required = "human_annotations.view_annotationschema"

    def get_queryset(self):
        return AnnotationSchema.objects.filter(team=self.request.team)


class CreateAnnotationSchema(LoginAndTeamRequiredMixin, CreateView, PermissionRequiredMixin):
    permission_required = "human_annotations.add_annotationschema"
    model = AnnotationSchema
    form_class = AnnotationSchemaForm
    template_name = "generic/object_form.html"
    extra_context = {
        "title": "Create Annotation Schema",
        "button_text": "Create",
        "active_tab": "annotation_schemas",
    }

    def get_success_url(self):
        return reverse("human_annotations:schema_home", args=[self.request.team.slug])

    def form_valid(self, form):
        form.instance.team = self.request.team
        return super().form_valid(form)


class EditAnnotationSchema(LoginAndTeamRequiredMixin, UpdateView, PermissionRequiredMixin):
    permission_required = "human_annotations.change_annotationschema"
    model = AnnotationSchema
    form_class = AnnotationSchemaForm
    template_name = "generic/object_form.html"
    extra_context = {
        "title": "Edit Annotation Schema",
        "button_text": "Update",
        "active_tab": "annotation_schemas",
    }

    def get_queryset(self):
        return AnnotationSchema.objects.filter(team=self.request.team)

    def get_success_url(self):
        return reverse("human_annotations:schema_home", args=[self.request.team.slug])


class DeleteAnnotationSchema(LoginAndTeamRequiredMixin, View, PermissionRequiredMixin):
    permission_required = "human_annotations.delete_annotationschema"

    def delete(self, request, team_slug: str, pk: int):
        from django.http import HttpResponse
        from django.shortcuts import get_object_or_404

        schema = get_object_or_404(AnnotationSchema, id=pk, team=request.team)
        if schema.queues.exists():
            from django.contrib import messages
            messages.error(request, "Cannot delete schema that is in use by queues.")
            return HttpResponse(status=400)
        schema.delete()
        return HttpResponse()
```

**Step 6: Wire up URLs**

```python
# apps/human_annotations/urls.py
from django.urls import path

from apps.generics.urls import make_crud_urls
from apps.human_annotations.views import schema_views

app_name = "human_annotations"

urlpatterns = []
urlpatterns.extend(make_crud_urls(schema_views, "AnnotationSchema", "schema"))
```

**Step 7: Update views/__init__.py**

```python
# apps/human_annotations/views/__init__.py
# Views organized in submodules
```

**Step 8: Run tests**

Run: `pytest apps/human_annotations/tests/test_views.py -v`
Expected: All tests PASS

**Step 9: Lint and commit**

Run: `ruff check apps/human_annotations/ --fix && ruff format apps/human_annotations/`

```bash
git add apps/human_annotations/
git commit -m "feat: add annotation schema CRUD views with validation"
```

---

## Task 7: Queue CRUD Views

**Files:**
- Create: `apps/human_annotations/views/queue_views.py`
- Modify: `apps/human_annotations/forms.py`
- Modify: `apps/human_annotations/tables.py`
- Modify: `apps/human_annotations/urls.py`
- Modify: `apps/human_annotations/tests/test_views.py`

**Context:** Queue management views: Home (list), Create, Edit, Detail (shows items and progress), Delete. The queue detail page is the admin dashboard for a single queue. Use `make_crud_urls` for the list/create/edit/delete. Add a separate detail view for queue management.

**Step 1: Write the failing tests**

Append to `apps/human_annotations/tests/test_views.py`:

```python
from apps.human_annotations.models import AnnotationQueue
from apps.utils.factories.human_annotations import AnnotationSchemaFactory


@pytest.mark.django_db()
def test_queue_home(logged_in_client, team):
    url = reverse("human_annotations:queue_home", kwargs={"team_slug": team.slug})
    response = logged_in_client.get(url)
    assert response.status_code == 200


@pytest.mark.django_db()
def test_create_queue(logged_in_client, team):
    schema = AnnotationSchemaFactory(team=team)
    url = reverse("human_annotations:queue_new", kwargs={"team_slug": team.slug})
    response = logged_in_client.post(url, data={
        "name": "My Queue",
        "description": "Test queue",
        "schema": schema.id,
        "num_reviews_required": 3,
    })
    assert response.status_code == 302
    queue = AnnotationQueue.objects.get(team=team, name="My Queue")
    assert queue.num_reviews_required == 3
    assert queue.created_by == team.members.first()
```

**Step 2: Run test to verify it fails**

Run: `pytest apps/human_annotations/tests/test_views.py::test_queue_home -v`
Expected: FAIL

**Step 3: Write the form**

Add to `apps/human_annotations/forms.py`:

```python
from .models import AnnotationQueue


class AnnotationQueueForm(forms.ModelForm):
    class Meta:
        model = AnnotationQueue
        fields = ["name", "description", "schema", "num_reviews_required"]
        widgets = {
            "num_reviews_required": forms.NumberInput(attrs={"min": 1, "max": 10}),
        }

    def __init__(self, team, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["schema"].queryset = AnnotationSchema.objects.filter(team=team)

    def clean_num_reviews_required(self):
        value = self.cleaned_data["num_reviews_required"]
        if not (1 <= value <= 10):
            raise ValidationError("Must be between 1 and 10")
        return value
```

**Step 4: Write the table**

Add to `apps/human_annotations/tables.py`:

```python
from .models import AnnotationQueue


class AnnotationQueueTable(tables.Table):
    name = tables.Column(linkify=True)
    progress = tables.Column(verbose_name="Progress", empty_values=(), orderable=False)

    class Meta:
        model = AnnotationQueue
        fields = ["name", "schema", "status", "num_reviews_required", "progress", "created_at"]
        attrs = {"class": "table"}

    def render_progress(self, record):
        progress = record.get_progress()
        return f"{progress['completed']}/{progress['total']} ({progress['percent']}%)"
```

**Step 5: Write the views**

```python
# apps/human_annotations/views/queue_views.py
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, TemplateView, UpdateView
from django_tables2 import SingleTableView

from apps.teams.mixins import LoginAndTeamRequiredMixin

from ..forms import AnnotationQueueForm
from ..models import AnnotationQueue
from ..tables import AnnotationQueueTable


class AnnotationQueueHome(LoginAndTeamRequiredMixin, TemplateView, PermissionRequiredMixin):
    template_name = "generic/object_home.html"
    permission_required = "human_annotations.view_annotationqueue"

    def get_context_data(self, team_slug: str, **kwargs):
        return {
            "active_tab": "annotation_queues",
            "title": "Annotation Queues",
            "new_object_url": reverse("human_annotations:queue_new", args=[team_slug]),
            "table_url": reverse("human_annotations:queue_table", args=[team_slug]),
            "enable_search": True,
        }


class AnnotationQueueTableView(LoginAndTeamRequiredMixin, SingleTableView, PermissionRequiredMixin):
    model = AnnotationQueue
    table_class = AnnotationQueueTable
    template_name = "table/single_table.html"
    permission_required = "human_annotations.view_annotationqueue"

    def get_queryset(self):
        return AnnotationQueue.objects.filter(team=self.request.team)


class CreateAnnotationQueue(LoginAndTeamRequiredMixin, CreateView, PermissionRequiredMixin):
    permission_required = "human_annotations.add_annotationqueue"
    model = AnnotationQueue
    form_class = AnnotationQueueForm
    template_name = "generic/object_form.html"
    extra_context = {
        "title": "Create Annotation Queue",
        "button_text": "Create",
        "active_tab": "annotation_queues",
    }

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["team"] = self.request.team
        return kwargs

    def get_success_url(self):
        return reverse("human_annotations:queue_home", args=[self.request.team.slug])

    def form_valid(self, form):
        form.instance.team = self.request.team
        form.instance.created_by = self.request.user
        return super().form_valid(form)


class EditAnnotationQueue(LoginAndTeamRequiredMixin, UpdateView, PermissionRequiredMixin):
    permission_required = "human_annotations.change_annotationqueue"
    model = AnnotationQueue
    form_class = AnnotationQueueForm
    template_name = "generic/object_form.html"
    extra_context = {
        "title": "Edit Annotation Queue",
        "button_text": "Update",
        "active_tab": "annotation_queues",
    }

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["team"] = self.request.team
        return kwargs

    def get_queryset(self):
        return AnnotationQueue.objects.filter(team=self.request.team)

    def get_success_url(self):
        return reverse("human_annotations:queue_home", args=[self.request.team.slug])


class DeleteAnnotationQueue(LoginAndTeamRequiredMixin, View, PermissionRequiredMixin):
    permission_required = "human_annotations.delete_annotationqueue"

    def delete(self, request, team_slug: str, pk: int):
        queue = get_object_or_404(AnnotationQueue, id=pk, team=request.team)
        queue.delete()
        return HttpResponse()
```

**Step 6: Wire up URLs**

Update `apps/human_annotations/urls.py`:

```python
from django.urls import path

from apps.generics.urls import make_crud_urls
from apps.human_annotations.views import queue_views, schema_views

app_name = "human_annotations"

urlpatterns = []
urlpatterns.extend(make_crud_urls(schema_views, "AnnotationSchema", "schema"))
urlpatterns.extend(make_crud_urls(queue_views, "AnnotationQueue", "queue"))
```

**Step 7: Run tests**

Run: `pytest apps/human_annotations/tests/test_views.py -v`
Expected: All tests PASS

**Step 8: Lint and commit**

Run: `ruff check apps/human_annotations/ --fix && ruff format apps/human_annotations/`

```bash
git add apps/human_annotations/
git commit -m "feat: add annotation queue CRUD views"
```

---

## Task 8: Queue Detail View (Admin Dashboard)

**Files:**
- Modify: `apps/human_annotations/views/queue_views.py`
- Create: `templates/human_annotations/queue_detail.html`
- Create: `templates/human_annotations/components/items_table.html`
- Modify: `apps/human_annotations/tables.py`
- Modify: `apps/human_annotations/urls.py`
- Modify: `apps/human_annotations/tests/test_views.py`

**Context:** The queue detail page is the admin dashboard. It shows: queue metadata, progress bar, assignee list, and a table of annotation items with their statuses and review counts. Items table loaded via HTMX.

**Step 1: Write the failing test**

Append to `apps/human_annotations/tests/test_views.py`:

```python
from apps.utils.factories.human_annotations import AnnotationQueueFactory


@pytest.mark.django_db()
def test_queue_detail(logged_in_client, team):
    queue = AnnotationQueueFactory(team=team)
    url = reverse("human_annotations:queue_detail", kwargs={"team_slug": team.slug, "pk": queue.pk})
    response = logged_in_client.get(url)
    assert response.status_code == 200
```

**Step 2: Run test to verify it fails**

Run: `pytest apps/human_annotations/tests/test_views.py::test_queue_detail -v`
Expected: FAIL

**Step 3: Write the items table**

Add to `apps/human_annotations/tables.py`:

```python
from .models import AnnotationItem


class AnnotationItemTable(tables.Table):
    item_type = tables.Column(verbose_name="Type")
    description = tables.Column(verbose_name="Description", empty_values=(), orderable=False)
    status = tables.Column()
    review_count = tables.Column(verbose_name="Reviews")

    class Meta:
        model = AnnotationItem
        fields = ["item_type", "description", "status", "review_count", "created_at"]
        attrs = {"class": "table"}

    def render_description(self, record):
        return str(record)

    def render_review_count(self, record):
        return f"{record.review_count}/{record.queue.num_reviews_required}"
```

**Step 4: Write the view**

Add to `apps/human_annotations/views/queue_views.py`:

```python
from django.views.generic import DetailView
from ..models import AnnotationItem
from ..tables import AnnotationItemTable


class AnnotationQueueDetail(LoginAndTeamRequiredMixin, DetailView, PermissionRequiredMixin):
    model = AnnotationQueue
    template_name = "human_annotations/queue_detail.html"
    permission_required = "human_annotations.view_annotationqueue"

    def get_queryset(self):
        return AnnotationQueue.objects.filter(team=self.request.team)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        queue = self.object
        context["active_tab"] = "annotation_queues"
        context["progress"] = queue.get_progress()
        context["items_table_url"] = reverse(
            "human_annotations:queue_items_table",
            args=[self.request.team.slug, queue.pk],
        )
        return context


class AnnotationQueueItemsTableView(LoginAndTeamRequiredMixin, SingleTableView, PermissionRequiredMixin):
    model = AnnotationItem
    table_class = AnnotationItemTable
    template_name = "table/single_table.html"
    permission_required = "human_annotations.view_annotationqueue"

    def get_queryset(self):
        return AnnotationItem.objects.filter(
            queue_id=self.kwargs["pk"],
            queue__team=self.request.team,
        ).select_related("session", "message", "queue")
```

**Step 5: Write the template**

```html
<!-- templates/human_annotations/queue_detail.html -->
{% extends "generic/app_page.html" %}
{% load static %}

{% block app %}
<div class="flex flex-col gap-4">
  <!-- Header -->
  <div class="flex justify-between items-center">
    <div>
      <h2 class="text-xl font-bold">{{ object.name }}</h2>
      {% if object.description %}<p class="text-gray-500">{{ object.description }}</p>{% endif %}
    </div>
    <div class="flex gap-2">
      <span class="badge badge-{{ object.status }}">{{ object.get_status_display }}</span>
    </div>
  </div>

  <!-- Progress -->
  <div class="card bg-base-100 shadow-sm">
    <div class="card-body">
      <h3 class="card-title text-sm">Progress</h3>
      <progress class="progress progress-primary w-full" value="{{ progress.percent }}" max="100"></progress>
      <p class="text-sm text-gray-500">{{ progress.completed }} of {{ progress.total }} items completed ({{ progress.percent }}%)</p>
      <p class="text-sm text-gray-500">Reviews required per item: {{ object.num_reviews_required }}</p>
    </div>
  </div>

  <!-- Assignees -->
  <div class="card bg-base-100 shadow-sm">
    <div class="card-body">
      <h3 class="card-title text-sm">Assignees</h3>
      <div class="flex flex-wrap gap-2">
        {% for user in object.assignees.all %}
          <span class="badge badge-neutral">{{ user.get_full_name|default:user.username }}</span>
        {% empty %}
          <span class="text-gray-400 text-sm">No assignees</span>
        {% endfor %}
      </div>
    </div>
  </div>

  <!-- Items table -->
  <div class="card bg-base-100 shadow-sm">
    <div class="card-body">
      <h3 class="card-title text-sm">Items</h3>
      <div id="items-table"
           hx-get="{{ items_table_url }}"
           hx-trigger="load"
           hx-swap="innerHTML">
        <span class="loading loading-spinner loading-md"></span>
      </div>
    </div>
  </div>
</div>
{% endblock %}
```

**Step 6: Wire up URL**

Add to `apps/human_annotations/urls.py` (before the `make_crud_urls` extensions):

```python
urlpatterns = [
    path(
        "queue/<int:pk>/detail/",
        queue_views.AnnotationQueueDetail.as_view(),
        name="queue_detail",
    ),
    path(
        "queue/<int:pk>/items-table/",
        queue_views.AnnotationQueueItemsTableView.as_view(),
        name="queue_items_table",
    ),
]
urlpatterns.extend(make_crud_urls(schema_views, "AnnotationSchema", "schema"))
urlpatterns.extend(make_crud_urls(queue_views, "AnnotationQueue", "queue"))
```

**Step 7: Run tests**

Run: `pytest apps/human_annotations/tests/test_views.py -v`
Expected: All tests PASS

**Step 8: Lint and commit**

Run: `ruff check apps/human_annotations/ --fix && ruff format apps/human_annotations/`

```bash
git add apps/human_annotations/ templates/human_annotations/
git commit -m "feat: add queue detail view with progress and items table"
```

---

## Task 9: Bulk Add Items from Sessions

**Files:**
- Modify: `apps/human_annotations/views/queue_views.py`
- Modify: `apps/human_annotations/forms.py`
- Create: `templates/human_annotations/add_items_from_sessions.html`
- Modify: `apps/human_annotations/urls.py`
- Modify: `apps/human_annotations/tests/test_views.py`

**Context:** Admin can bulk add items to a queue by selecting sessions. This follows the existing pattern from evaluations (`EvaluationMessage.create_from_sessions`). The form lets users select an experiment, then filter sessions. Selected sessions are added as `AnnotationItem` records with `item_type=SESSION`. Duplicates are skipped.

**Step 1: Write the failing test**

Append to `apps/human_annotations/tests/test_views.py`:

```python
from apps.human_annotations.models import AnnotationItem
from apps.utils.factories.experiment import ExperimentSessionFactory


@pytest.mark.django_db()
def test_add_items_from_sessions(logged_in_client, team):
    queue = AnnotationQueueFactory(team=team)
    session1 = ExperimentSessionFactory(team=team, chat__team=team)
    session2 = ExperimentSessionFactory(team=team, chat__team=team)

    url = reverse("human_annotations:queue_add_sessions", kwargs={"team_slug": team.slug, "pk": queue.pk})
    response = logged_in_client.post(url, data={"sessions": [session1.id, session2.id]})
    assert response.status_code == 302
    assert AnnotationItem.objects.filter(queue=queue).count() == 2


@pytest.mark.django_db()
def test_add_items_skips_duplicates(logged_in_client, team):
    queue = AnnotationQueueFactory(team=team)
    session = ExperimentSessionFactory(team=team, chat__team=team)
    AnnotationItem.objects.create(queue=queue, team=team, item_type="session", session=session)

    url = reverse("human_annotations:queue_add_sessions", kwargs={"team_slug": team.slug, "pk": queue.pk})
    response = logged_in_client.post(url, data={"sessions": [session.id]})
    assert response.status_code == 302
    assert AnnotationItem.objects.filter(queue=queue).count() == 1  # no duplicate
```

**Step 2: Run test to verify it fails**

Run: `pytest apps/human_annotations/tests/test_views.py::test_add_items_from_sessions -v`
Expected: FAIL

**Step 3: Write the form**

Add to `apps/human_annotations/forms.py`:

```python
from apps.experiments.models import ExperimentSession


class AddSessionsToQueueForm(forms.Form):
    sessions = forms.ModelMultipleChoiceField(
        queryset=ExperimentSession.objects.none(),
        widget=forms.CheckboxSelectMultiple,
    )

    def __init__(self, team, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["sessions"].queryset = ExperimentSession.objects.filter(team=team).select_related(
            "experiment", "participant", "chat",
        ).order_by("-last_activity_at")
```

**Step 4: Write the view**

Add to `apps/human_annotations/views/queue_views.py`:

```python
from django.contrib import messages
from django.shortcuts import redirect

from ..forms import AddSessionsToQueueForm
from ..models import AnnotationItem, AnnotationItemType


class AddSessionsToQueue(LoginAndTeamRequiredMixin, View, PermissionRequiredMixin):
    permission_required = "human_annotations.add_annotationitem"

    def get(self, request, team_slug: str, pk: int):
        from django.shortcuts import render

        queue = get_object_or_404(AnnotationQueue, id=pk, team=request.team)
        form = AddSessionsToQueueForm(team=request.team)
        return render(request, "human_annotations/add_items_from_sessions.html", {
            "queue": queue,
            "form": form,
            "active_tab": "annotation_queues",
        })

    def post(self, request, team_slug: str, pk: int):
        queue = get_object_or_404(AnnotationQueue, id=pk, team=request.team)
        form = AddSessionsToQueueForm(request.team, request.POST)

        if form.is_valid():
            sessions = form.cleaned_data["sessions"]
            existing_session_ids = set(
                AnnotationItem.objects.filter(
                    queue=queue, session__in=sessions,
                ).values_list("session_id", flat=True)
            )

            items_to_create = [
                AnnotationItem(
                    queue=queue,
                    team=request.team,
                    item_type=AnnotationItemType.SESSION,
                    session=session,
                )
                for session in sessions
                if session.id not in existing_session_ids
            ]
            created = AnnotationItem.objects.bulk_create(items_to_create)
            skipped = len(sessions) - len(created)

            msg = f"Added {len(created)} items to queue."
            if skipped:
                msg += f" Skipped {skipped} duplicates."
            messages.success(request, msg)
        else:
            messages.error(request, "Invalid selection.")

        return redirect("human_annotations:queue_detail", team_slug=team_slug, pk=pk)
```

**Step 5: Write the template**

```html
<!-- templates/human_annotations/add_items_from_sessions.html -->
{% extends "generic/app_page.html" %}

{% block app %}
<div class="flex flex-col gap-4">
  <h2 class="text-xl font-bold">Add Sessions to "{{ queue.name }}"</h2>

  <form method="post">
    {% csrf_token %}
    <div class="form-control">
      <label class="label"><span class="label-text">Select sessions to add:</span></label>
      <div class="max-h-96 overflow-y-auto border rounded p-2">
        {{ form.sessions }}
      </div>
    </div>
    <div class="mt-4 flex gap-2">
      <button type="submit" class="btn btn-primary">Add to Queue</button>
      <a href="{% url 'human_annotations:queue_detail' team_slug=request.team.slug pk=queue.pk %}" class="btn btn-ghost">Cancel</a>
    </div>
  </form>
</div>
{% endblock %}
```

**Step 6: Wire up URL**

Add to `apps/human_annotations/urls.py`:

```python
path(
    "queue/<int:pk>/add-sessions/",
    queue_views.AddSessionsToQueue.as_view(),
    name="queue_add_sessions",
),
```

**Step 7: Run tests**

Run: `pytest apps/human_annotations/tests/test_views.py -v`
Expected: All tests PASS

**Step 8: Lint and commit**

Run: `ruff check apps/human_annotations/ --fix && ruff format apps/human_annotations/`

```bash
git add apps/human_annotations/ templates/human_annotations/
git commit -m "feat: add bulk session-to-queue import with duplicate prevention"
```

---

## Task 10: CSV Import for External Items

**Files:**
- Modify: `apps/human_annotations/tasks.py`
- Modify: `apps/human_annotations/views/queue_views.py`
- Modify: `apps/human_annotations/forms.py`
- Create: `templates/human_annotations/import_csv.html`
- Modify: `apps/human_annotations/urls.py`
- Create: `apps/human_annotations/tests/test_csv_import.py`

**Context:** Admin can upload a CSV file whose rows become external `AnnotationItem` records. Each row is stored as a JSON dict in `external_data`. The import runs as a Celery task for large files. Small files (< 100 rows) are imported synchronously.

**Step 1: Write the failing test**

```python
# apps/human_annotations/tests/test_csv_import.py
import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.human_annotations.models import AnnotationItem
from apps.utils.factories.human_annotations import AnnotationQueueFactory
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def team():
    return TeamWithUsersFactory()


@pytest.fixture()
def logged_in_client(team, client):
    user = team.members.first()
    client.login(username=user.username, password="password")
    return client


@pytest.mark.django_db()
def test_csv_import(logged_in_client, team):
    queue = AnnotationQueueFactory(team=team)
    csv_content = b"input,output,context\nHello,Hi there,greeting\nHow are you,I'm fine,chitchat"
    csv_file = SimpleUploadedFile("test.csv", csv_content, content_type="text/csv")

    url = reverse("human_annotations:queue_import_csv", kwargs={"team_slug": team.slug, "pk": queue.pk})
    response = logged_in_client.post(url, data={"csv_file": csv_file})
    assert response.status_code == 302
    assert AnnotationItem.objects.filter(queue=queue).count() == 2
    item = AnnotationItem.objects.filter(queue=queue).first()
    assert item.item_type == "external"
    assert item.external_data["input"] == "Hello"
```

**Step 2: Run test to verify it fails**

Run: `pytest apps/human_annotations/tests/test_csv_import.py -v`
Expected: FAIL

**Step 3: Write the form**

Add to `apps/human_annotations/forms.py`:

```python
class CSVImportForm(forms.Form):
    csv_file = forms.FileField(
        help_text="CSV file with headers. Each row becomes an annotation item.",
    )
```

**Step 4: Write the view**

Add to `apps/human_annotations/views/queue_views.py`:

```python
import csv
import io

from ..forms import CSVImportForm


class ImportCSVToQueue(LoginAndTeamRequiredMixin, View, PermissionRequiredMixin):
    permission_required = "human_annotations.add_annotationitem"

    def get(self, request, team_slug: str, pk: int):
        from django.shortcuts import render

        queue = get_object_or_404(AnnotationQueue, id=pk, team=request.team)
        form = CSVImportForm()
        return render(request, "human_annotations/import_csv.html", {
            "queue": queue,
            "form": form,
            "active_tab": "annotation_queues",
        })

    def post(self, request, team_slug: str, pk: int):
        queue = get_object_or_404(AnnotationQueue, id=pk, team=request.team)
        form = CSVImportForm(request.POST, request.FILES)

        if form.is_valid():
            csv_file = form.cleaned_data["csv_file"]
            decoded = csv_file.read().decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(decoded))
            rows = list(reader)

            items = [
                AnnotationItem(
                    queue=queue,
                    team=request.team,
                    item_type=AnnotationItemType.EXTERNAL,
                    external_data=dict(row),
                )
                for row in rows
            ]
            AnnotationItem.objects.bulk_create(items)
            messages.success(request, f"Imported {len(items)} items from CSV.")
        else:
            messages.error(request, "Invalid file.")

        return redirect("human_annotations:queue_detail", team_slug=team_slug, pk=pk)
```

**Step 5: Write the template**

```html
<!-- templates/human_annotations/import_csv.html -->
{% extends "generic/app_page.html" %}

{% block app %}
<div class="flex flex-col gap-4">
  <h2 class="text-xl font-bold">Import CSV to "{{ queue.name }}"</h2>

  <form method="post" enctype="multipart/form-data">
    {% csrf_token %}
    <div class="form-control">
      <label class="label"><span class="label-text">CSV file:</span></label>
      {{ form.csv_file }}
      <p class="text-xs text-gray-500 mt-1">Each row becomes an annotation item. Column headers become field names in the external data.</p>
    </div>
    <div class="mt-4 flex gap-2">
      <button type="submit" class="btn btn-primary">Import</button>
      <a href="{% url 'human_annotations:queue_detail' team_slug=request.team.slug pk=queue.pk %}" class="btn btn-ghost">Cancel</a>
    </div>
  </form>
</div>
{% endblock %}
```

**Step 6: Wire up URL**

Add to `apps/human_annotations/urls.py`:

```python
path(
    "queue/<int:pk>/import-csv/",
    queue_views.ImportCSVToQueue.as_view(),
    name="queue_import_csv",
),
```

**Step 7: Run tests**

Run: `pytest apps/human_annotations/tests/test_csv_import.py -v`
Expected: All tests PASS

**Step 8: Lint and commit**

Run: `ruff check apps/human_annotations/ --fix && ruff format apps/human_annotations/`

```bash
git add apps/human_annotations/ templates/human_annotations/
git commit -m "feat: add CSV import for external annotation items"
```

---

## Task 11: Annotator UI - Annotation View

**Files:**
- Create: `apps/human_annotations/views/annotate_views.py`
- Create: `templates/human_annotations/annotate.html`
- Modify: `apps/human_annotations/forms.py`
- Modify: `apps/human_annotations/urls.py`
- Create: `apps/human_annotations/tests/test_annotate.py`

**Context:** The annotator UI shows one item at a time. The annotator sees the item content (session messages, message content, or external data) and a form generated from the schema. Actions: Submit, Skip (moves to next without annotating), Flag (marks item as flagged). "Next item" logic: get the oldest pending/in-progress item in this queue that this user hasn't already annotated.

**Step 1: Write the failing tests**

```python
# apps/human_annotations/tests/test_annotate.py
import pytest
from django.urls import reverse

from apps.human_annotations.models import Annotation, AnnotationItem, AnnotationItemStatus
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.human_annotations import AnnotationItemFactory, AnnotationQueueFactory
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def team():
    return TeamWithUsersFactory()


@pytest.fixture()
def logged_in_client(team, client):
    user = team.members.first()
    client.login(username=user.username, password="password")
    return client


@pytest.mark.django_db()
def test_annotate_view_shows_next_item(logged_in_client, team):
    queue = AnnotationQueueFactory(team=team)
    queue.assignees.add(team.members.first())
    session = ExperimentSessionFactory(team=team, chat__team=team)
    AnnotationItem.objects.create(queue=queue, team=team, item_type="session", session=session)

    url = reverse("human_annotations:annotate_queue", kwargs={"team_slug": team.slug, "pk": queue.pk})
    response = logged_in_client.get(url)
    assert response.status_code == 200


@pytest.mark.django_db()
def test_submit_annotation(logged_in_client, team):
    schema_data = {"score": {"type": "int", "description": "Score", "ge": 1, "le": 5}}
    queue = AnnotationQueueFactory(team=team, schema__schema=schema_data)
    queue.assignees.add(team.members.first())
    session = ExperimentSessionFactory(team=team, chat__team=team)
    item = AnnotationItem.objects.create(queue=queue, team=team, item_type="session", session=session)

    url = reverse("human_annotations:submit_annotation", kwargs={
        "team_slug": team.slug, "pk": queue.pk, "item_pk": item.pk,
    })
    response = logged_in_client.post(url, data={"score": 4})
    assert response.status_code == 302
    assert Annotation.objects.filter(item=item).count() == 1
    item.refresh_from_db()
    assert item.review_count == 1


@pytest.mark.django_db()
def test_flag_item(logged_in_client, team):
    queue = AnnotationQueueFactory(team=team)
    queue.assignees.add(team.members.first())
    session = ExperimentSessionFactory(team=team, chat__team=team)
    item = AnnotationItem.objects.create(queue=queue, team=team, item_type="session", session=session)

    url = reverse("human_annotations:flag_item", kwargs={
        "team_slug": team.slug, "pk": queue.pk, "item_pk": item.pk,
    })
    response = logged_in_client.post(url)
    assert response.status_code == 302
    item.refresh_from_db()
    assert item.status == AnnotationItemStatus.FLAGGED
```

**Step 2: Run test to verify it fails**

Run: `pytest apps/human_annotations/tests/test_annotate.py -v`
Expected: FAIL

**Step 3: Write the dynamic form builder**

Add to `apps/human_annotations/forms.py`:

```python
from apps.evaluations.field_definitions import (
    ChoiceFieldDefinition,
    FloatFieldDefinition,
    IntFieldDefinition,
    StringFieldDefinition,
)


def build_annotation_form(schema_instance):
    """Dynamically build a Django form from an AnnotationSchema's field definitions."""
    field_defs = schema_instance.get_field_definitions()
    form_fields = {}

    for name, defn in field_defs.items():
        if isinstance(defn, IntFieldDefinition):
            kwargs = {"label": defn.description, "required": True}
            if defn.ge is not None:
                kwargs["min_value"] = defn.ge
            if defn.le is not None:
                kwargs["max_value"] = defn.le
            form_fields[name] = forms.IntegerField(**kwargs)

        elif isinstance(defn, FloatFieldDefinition):
            kwargs = {"label": defn.description, "required": True}
            if defn.ge is not None:
                kwargs["min_value"] = defn.ge
            if defn.le is not None:
                kwargs["max_value"] = defn.le
            form_fields[name] = forms.FloatField(**kwargs)

        elif isinstance(defn, ChoiceFieldDefinition):
            choices = [("", "---")] + [(c, c) for c in defn.choices]
            form_fields[name] = forms.ChoiceField(
                label=defn.description, choices=choices, required=True,
            )

        elif isinstance(defn, StringFieldDefinition):
            kwargs = {"label": defn.description, "required": True}
            if defn.max_length:
                kwargs["max_length"] = defn.max_length
            form_fields[name] = forms.CharField(
                widget=forms.Textarea(attrs={"rows": 3}), **kwargs,
            )

    return type("AnnotationForm", (forms.Form,), form_fields)
```

**Step 4: Write the views**

```python
# apps/human_annotations/views/annotate_views.py
from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from apps.teams.mixins import LoginAndTeamRequiredMixin

from ..forms import build_annotation_form
from ..models import (
    Annotation,
    AnnotationItem,
    AnnotationItemStatus,
    AnnotationQueue,
    AnnotationStatus,
)


def _get_next_item(queue, user):
    """Get the next item for this user to annotate: oldest pending/in-progress not already reviewed by user."""
    already_annotated = Annotation.objects.filter(
        item__queue=queue, reviewer=user,
    ).values_list("item_id", flat=True)

    return (
        AnnotationItem.objects.filter(
            queue=queue,
            status__in=[AnnotationItemStatus.PENDING, AnnotationItemStatus.IN_PROGRESS],
        )
        .exclude(id__in=already_annotated)
        .order_by("created_at")
        .first()
    )


def _get_progress_for_user(queue, user):
    """Get progress info for the current annotator."""
    total = queue.items.count()
    reviewed_by_user = Annotation.objects.filter(item__queue=queue, reviewer=user).count()
    return {"total": total, "reviewed": reviewed_by_user}


class AnnotateQueue(LoginAndTeamRequiredMixin, View, PermissionRequiredMixin):
    permission_required = "human_annotations.add_annotation"

    def get(self, request, team_slug: str, pk: int):
        queue = get_object_or_404(AnnotationQueue, id=pk, team=request.team)
        item = _get_next_item(queue, request.user)

        if item is None:
            messages.info(request, "No more items to annotate in this queue.")
            return redirect("human_annotations:queue_home", team_slug=team_slug)

        FormClass = build_annotation_form(queue.schema)
        form = FormClass()
        progress = _get_progress_for_user(queue, request.user)

        # Load item content for display
        item_content = _get_item_display_content(item)

        return render(request, "human_annotations/annotate.html", {
            "queue": queue,
            "item": item,
            "form": form,
            "progress": progress,
            "item_content": item_content,
            "active_tab": "annotation_queues",
        })


class SubmitAnnotation(LoginAndTeamRequiredMixin, View, PermissionRequiredMixin):
    permission_required = "human_annotations.add_annotation"

    def post(self, request, team_slug: str, pk: int, item_pk: int):
        queue = get_object_or_404(AnnotationQueue, id=pk, team=request.team)
        item = get_object_or_404(AnnotationItem, id=item_pk, queue=queue)

        # Check for duplicate
        if Annotation.objects.filter(item=item, reviewer=request.user).exists():
            messages.warning(request, "You've already annotated this item.")
            return redirect("human_annotations:annotate_queue", team_slug=team_slug, pk=pk)

        FormClass = build_annotation_form(queue.schema)
        form = FormClass(request.POST)

        if form.is_valid():
            Annotation.objects.create(
                item=item,
                team=request.team,
                reviewer=request.user,
                data=form.cleaned_data,
                status=AnnotationStatus.SUBMITTED,
            )
            messages.success(request, "Annotation submitted.")
        else:
            messages.error(request, "Invalid annotation data. Please check the form.")
            # Re-render with errors
            item_content = _get_item_display_content(item)
            progress = _get_progress_for_user(queue, request.user)
            return render(request, "human_annotations/annotate.html", {
                "queue": queue,
                "item": item,
                "form": form,
                "progress": progress,
                "item_content": item_content,
                "active_tab": "annotation_queues",
            })

        return redirect("human_annotations:annotate_queue", team_slug=team_slug, pk=pk)


class FlagItem(LoginAndTeamRequiredMixin, View, PermissionRequiredMixin):
    permission_required = "human_annotations.change_annotationitem"

    def post(self, request, team_slug: str, pk: int, item_pk: int):
        queue = get_object_or_404(AnnotationQueue, id=pk, team=request.team)
        item = get_object_or_404(AnnotationItem, id=item_pk, queue=queue)
        item.status = AnnotationItemStatus.FLAGGED
        item.save(update_fields=["status"])
        messages.info(request, "Item flagged for review.")
        return redirect("human_annotations:annotate_queue", team_slug=team_slug, pk=pk)


def _get_item_display_content(item):
    """Build display content dict for the annotation UI."""
    if item.session_id:
        chat_messages = item.session.chat.messages.order_by("created_at").values_list(
            "message_type", "content",
        )
        return {
            "type": "session",
            "messages": [{"role": role, "content": content} for role, content in chat_messages],
            "participant": item.session.participant.identifier,
        }
    elif item.message_id:
        msg = item.message
        return {
            "type": "message",
            "role": msg.message_type,
            "content": msg.content,
        }
    else:
        return {
            "type": "external",
            "data": item.external_data,
        }
```

**Step 5: Write the template**

```html
<!-- templates/human_annotations/annotate.html -->
{% extends "generic/app_page.html" %}

{% block app %}
<div class="flex flex-col gap-4 max-w-4xl mx-auto">
  <!-- Header with progress -->
  <div class="flex justify-between items-center">
    <h2 class="text-xl font-bold">{{ queue.name }}</h2>
    <span class="badge badge-lg">{{ progress.reviewed }} of {{ progress.total }} reviewed</span>
  </div>

  <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
    <!-- Item content panel -->
    <div class="card bg-base-100 shadow-sm">
      <div class="card-body">
        <h3 class="card-title text-sm">Item Content</h3>
        {% if item_content.type == "session" %}
          <p class="text-xs text-gray-500 mb-2">Participant: {{ item_content.participant }}</p>
          <div class="flex flex-col gap-2 max-h-96 overflow-y-auto">
            {% for msg in item_content.messages %}
              <div class="chat {% if msg.role == 'human' %}chat-end{% else %}chat-start{% endif %}">
                <div class="chat-bubble {% if msg.role == 'human' %}chat-bubble-primary{% else %}chat-bubble-secondary{% endif %} text-sm">
                  {{ msg.content }}
                </div>
              </div>
            {% endfor %}
          </div>
        {% elif item_content.type == "message" %}
          <div class="prose text-sm">
            <strong>{{ item_content.role }}:</strong> {{ item_content.content }}
          </div>
        {% elif item_content.type == "external" %}
          <div class="overflow-x-auto">
            <table class="table table-sm">
              {% for key, value in item_content.data.items %}
                <tr><td class="font-medium">{{ key }}</td><td>{{ value }}</td></tr>
              {% endfor %}
            </table>
          </div>
        {% endif %}
      </div>
    </div>

    <!-- Annotation form panel -->
    <div class="card bg-base-100 shadow-sm">
      <div class="card-body">
        <h3 class="card-title text-sm">Annotation</h3>
        <form method="post" action="{% url 'human_annotations:submit_annotation' team_slug=request.team.slug pk=queue.pk item_pk=item.pk %}">
          {% csrf_token %}
          {% for field in form %}
            <div class="form-control mb-3">
              <label class="label"><span class="label-text">{{ field.label }}</span></label>
              {{ field }}
              {% if field.errors %}
                <span class="text-error text-xs">{{ field.errors.0 }}</span>
              {% endif %}
            </div>
          {% endfor %}
          <div class="flex gap-2 mt-4">
            <button type="submit" class="btn btn-primary btn-sm">Submit</button>
            <a href="{% url 'human_annotations:annotate_queue' team_slug=request.team.slug pk=queue.pk %}"
               class="btn btn-ghost btn-sm">Skip</a>
            <form method="post" action="{% url 'human_annotations:flag_item' team_slug=request.team.slug pk=queue.pk item_pk=item.pk %}" class="inline">
              {% csrf_token %}
              <button type="submit" class="btn btn-warning btn-sm btn-outline">Flag</button>
            </form>
          </div>
        </form>
      </div>
    </div>
  </div>
</div>
{% endblock %}
```

Note: There's a nested form issue in the template above (form inside form). Fix this by moving the Flag button outside or using a separate div with HTMX:

Replace the flag button section with:
```html
<div class="flex gap-2 mt-4">
  <button type="submit" class="btn btn-primary btn-sm">Submit</button>
  <a href="{% url 'human_annotations:annotate_queue' team_slug=request.team.slug pk=queue.pk %}"
     class="btn btn-ghost btn-sm">Skip</a>
  <button type="button" class="btn btn-warning btn-sm btn-outline"
          hx-post="{% url 'human_annotations:flag_item' team_slug=request.team.slug pk=queue.pk item_pk=item.pk %}"
          hx-headers='{"X-CSRFToken": "{{ csrf_token }}"}'>Flag</button>
</div>
```

**Step 6: Wire up URLs**

Add to `apps/human_annotations/urls.py`:

```python
path(
    "queue/<int:pk>/annotate/",
    annotate_views.AnnotateQueue.as_view(),
    name="annotate_queue",
),
path(
    "queue/<int:pk>/item/<int:item_pk>/submit/",
    annotate_views.SubmitAnnotation.as_view(),
    name="submit_annotation",
),
path(
    "queue/<int:pk>/item/<int:item_pk>/flag/",
    annotate_views.FlagItem.as_view(),
    name="flag_item",
),
```

And import at top:
```python
from apps.human_annotations.views import annotate_views, queue_views, schema_views
```

**Step 7: Run tests**

Run: `pytest apps/human_annotations/tests/test_annotate.py -v`
Expected: All tests PASS

**Step 8: Lint and commit**

Run: `ruff check apps/human_annotations/ --fix && ruff format apps/human_annotations/`

```bash
git add apps/human_annotations/ templates/human_annotations/
git commit -m "feat: add annotator UI with dynamic form generation and flag/skip"
```

---

## Task 12: Export Annotations (CSV/JSONL)

**Files:**
- Modify: `apps/human_annotations/views/queue_views.py`
- Modify: `apps/human_annotations/urls.py`
- Create: `apps/human_annotations/tests/test_export.py`

**Context:** Admin can export all annotations for a queue as CSV or JSONL. Each row contains the item data plus all annotations and reviewer info.

**Step 1: Write the failing test**

```python
# apps/human_annotations/tests/test_export.py
import csv
import io
import json

import pytest
from django.urls import reverse

from apps.human_annotations.models import Annotation, AnnotationItem
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.human_annotations import AnnotationQueueFactory
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def team():
    return TeamWithUsersFactory()


@pytest.fixture()
def logged_in_client(team, client):
    user = team.members.first()
    client.login(username=user.username, password="password")
    return client


@pytest.mark.django_db()
def test_export_csv(logged_in_client, team):
    queue = AnnotationQueueFactory(team=team, schema__schema={
        "score": {"type": "int", "description": "Score", "ge": 1, "le": 5},
    })
    session = ExperimentSessionFactory(team=team, chat__team=team)
    item = AnnotationItem.objects.create(queue=queue, team=team, item_type="session", session=session)
    user = team.members.first()
    Annotation.objects.create(item=item, team=team, reviewer=user, data={"score": 4})

    url = reverse("human_annotations:queue_export", kwargs={"team_slug": team.slug, "pk": queue.pk})
    response = logged_in_client.get(url, {"format": "csv"})
    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv"
    content = response.content.decode()
    reader = csv.DictReader(io.StringIO(content))
    rows = list(reader)
    assert len(rows) == 1
    assert rows[0]["score"] == "4"


@pytest.mark.django_db()
def test_export_jsonl(logged_in_client, team):
    queue = AnnotationQueueFactory(team=team, schema__schema={
        "score": {"type": "int", "description": "Score", "ge": 1, "le": 5},
    })
    session = ExperimentSessionFactory(team=team, chat__team=team)
    item = AnnotationItem.objects.create(queue=queue, team=team, item_type="session", session=session)
    user = team.members.first()
    Annotation.objects.create(item=item, team=team, reviewer=user, data={"score": 4})

    url = reverse("human_annotations:queue_export", kwargs={"team_slug": team.slug, "pk": queue.pk})
    response = logged_in_client.get(url, {"format": "jsonl"})
    assert response.status_code == 200
    lines = response.content.decode().strip().split("\n")
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["annotation"]["score"] == 4
```

**Step 2: Run test to verify it fails**

Run: `pytest apps/human_annotations/tests/test_export.py -v`
Expected: FAIL

**Step 3: Write the view**

Add to `apps/human_annotations/views/queue_views.py`:

```python
import csv as csv_module
import json

from django.http import HttpResponse, StreamingHttpResponse


class ExportAnnotations(LoginAndTeamRequiredMixin, View, PermissionRequiredMixin):
    permission_required = "human_annotations.view_annotation"

    def get(self, request, team_slug: str, pk: int):
        queue = get_object_or_404(AnnotationQueue, id=pk, team=request.team)
        export_format = request.GET.get("format", "csv")
        annotations = Annotation.objects.filter(
            item__queue=queue, status="submitted",
        ).select_related("item", "item__session", "item__message", "reviewer")

        if export_format == "jsonl":
            return self._export_jsonl(queue, annotations)
        return self._export_csv(queue, annotations)

    def _export_csv(self, queue, annotations):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{queue.name}_annotations.csv"'

        schema_fields = list(queue.schema.schema.keys())
        fieldnames = ["item_id", "item_type", "reviewer", "annotated_at"] + schema_fields

        writer = csv_module.DictWriter(response, fieldnames=fieldnames)
        writer.writeheader()

        for ann in annotations:
            row = {
                "item_id": ann.item_id,
                "item_type": ann.item.item_type,
                "reviewer": ann.reviewer.get_full_name() or ann.reviewer.username,
                "annotated_at": ann.created_at.isoformat(),
            }
            for field in schema_fields:
                row[field] = ann.data.get(field, "")
            writer.writerow(row)

        return response

    def _export_jsonl(self, queue, annotations):
        lines = []
        for ann in annotations:
            record = {
                "item_id": ann.item_id,
                "item_type": ann.item.item_type,
                "reviewer": ann.reviewer.get_full_name() or ann.reviewer.username,
                "annotated_at": ann.created_at.isoformat(),
                "annotation": ann.data,
            }
            if ann.item.external_data:
                record["external_data"] = ann.item.external_data
            lines.append(json.dumps(record))

        content = "\n".join(lines)
        response = HttpResponse(content, content_type="application/jsonl")
        response["Content-Disposition"] = f'attachment; filename="{queue.name}_annotations.jsonl"'
        return response
```

**Step 4: Wire up URL**

Add to `apps/human_annotations/urls.py`:

```python
path(
    "queue/<int:pk>/export/",
    queue_views.ExportAnnotations.as_view(),
    name="queue_export",
),
```

**Step 5: Run tests**

Run: `pytest apps/human_annotations/tests/test_export.py -v`
Expected: All tests PASS

**Step 6: Lint and commit**

Run: `ruff check apps/human_annotations/ --fix && ruff format apps/human_annotations/`

```bash
git add apps/human_annotations/
git commit -m "feat: add CSV and JSONL export for annotation queues"
```

---

## Task 13: Queue Assignee Management

**Files:**
- Modify: `apps/human_annotations/views/queue_views.py`
- Create: `templates/human_annotations/manage_assignees.html`
- Modify: `apps/human_annotations/urls.py`
- Modify: `apps/human_annotations/tests/test_views.py`

**Context:** Admins need to assign/unassign users to queues. Add a simple view that shows current assignees and allows adding/removing team members.

**Step 1: Write the failing test**

Append to `apps/human_annotations/tests/test_views.py`:

```python
@pytest.mark.django_db()
def test_manage_assignees(logged_in_client, team):
    queue = AnnotationQueueFactory(team=team)
    user = team.members.first()
    url = reverse("human_annotations:queue_manage_assignees", kwargs={"team_slug": team.slug, "pk": queue.pk})

    # Add assignee
    response = logged_in_client.post(url, data={"assignees": [user.id]})
    assert response.status_code == 302
    assert queue.assignees.count() == 1
```

**Step 2: Run test to verify it fails**

Run: `pytest apps/human_annotations/tests/test_views.py::test_manage_assignees -v`
Expected: FAIL

**Step 3: Write the form**

Add to `apps/human_annotations/forms.py`:

```python
from django.contrib.auth import get_user_model

User = get_user_model()


class ManageAssigneesForm(forms.Form):
    assignees = forms.ModelMultipleChoiceField(
        queryset=User.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )

    def __init__(self, team, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assignees"].queryset = User.objects.filter(
            membership__team=team,
        )
```

**Step 4: Write the view**

Add to `apps/human_annotations/views/queue_views.py`:

```python
from ..forms import ManageAssigneesForm


class ManageAssignees(LoginAndTeamRequiredMixin, View, PermissionRequiredMixin):
    permission_required = "human_annotations.change_annotationqueue"

    def get(self, request, team_slug: str, pk: int):
        from django.shortcuts import render

        queue = get_object_or_404(AnnotationQueue, id=pk, team=request.team)
        form = ManageAssigneesForm(
            team=request.team,
            initial={"assignees": queue.assignees.all()},
        )
        return render(request, "human_annotations/manage_assignees.html", {
            "queue": queue,
            "form": form,
            "active_tab": "annotation_queues",
        })

    def post(self, request, team_slug: str, pk: int):
        queue = get_object_or_404(AnnotationQueue, id=pk, team=request.team)
        form = ManageAssigneesForm(request.team, request.POST)

        if form.is_valid():
            queue.assignees.set(form.cleaned_data["assignees"])
            messages.success(request, "Assignees updated.")

        return redirect("human_annotations:queue_detail", team_slug=team_slug, pk=pk)
```

**Step 5: Write the template**

```html
<!-- templates/human_annotations/manage_assignees.html -->
{% extends "generic/app_page.html" %}

{% block app %}
<div class="flex flex-col gap-4 max-w-lg">
  <h2 class="text-xl font-bold">Manage Assignees for "{{ queue.name }}"</h2>

  <form method="post">
    {% csrf_token %}
    <div class="form-control">
      <label class="label"><span class="label-text">Select team members:</span></label>
      {{ form.assignees }}
    </div>
    <div class="mt-4 flex gap-2">
      <button type="submit" class="btn btn-primary">Save</button>
      <a href="{% url 'human_annotations:queue_detail' team_slug=request.team.slug pk=queue.pk %}" class="btn btn-ghost">Cancel</a>
    </div>
  </form>
</div>
{% endblock %}
```

**Step 6: Wire up URL**

Add to `apps/human_annotations/urls.py`:

```python
path(
    "queue/<int:pk>/assignees/",
    queue_views.ManageAssignees.as_view(),
    name="queue_manage_assignees",
),
```

**Step 7: Run tests**

Run: `pytest apps/human_annotations/tests/ -v`
Expected: All tests PASS

**Step 8: Lint and commit**

Run: `ruff check apps/human_annotations/ --fix && ruff format apps/human_annotations/`

```bash
git add apps/human_annotations/ templates/human_annotations/
git commit -m "feat: add queue assignee management"
```

---

## Task 14: Feature Flag Gating

**Files:**
- Modify: `apps/human_annotations/views/schema_views.py`
- Modify: `apps/human_annotations/views/queue_views.py`
- Modify: `apps/human_annotations/views/annotate_views.py`
- Modify: `apps/human_annotations/tests/test_views.py`

**Context:** Gate all human_annotations views behind a Waffle feature flag so it can be rolled out per-team. Follow the existing pattern from `apps/ocs_notifications`.

**Step 1: Write the failing test**

Add to `apps/human_annotations/tests/test_views.py`:

```python
from waffle.testutils import override_flag


@pytest.mark.django_db()
@override_flag("human_annotations", active=False)
def test_views_gated_by_flag(logged_in_client, team):
    url = reverse("human_annotations:queue_home", kwargs={"team_slug": team.slug})
    response = logged_in_client.get(url)
    assert response.status_code == 404
```

**Step 2: Run test to verify it fails**

Run: `pytest apps/human_annotations/tests/test_views.py::test_views_gated_by_flag -v`
Expected: FAIL (returns 200 instead of 404)

**Step 3: Add flag checking mixin**

Create a mixin or use the existing Waffle pattern. The simplest approach is a custom mixin:

Add to `apps/human_annotations/views/__init__.py`:

```python
from django.http import Http404
from waffle import flag_is_active


class HumanAnnotationsFeatureFlagMixin:
    """Mixin that gates views behind the human_annotations feature flag."""

    def dispatch(self, request, *args, **kwargs):
        if not flag_is_active(request, "human_annotations"):
            raise Http404
        return super().dispatch(request, *args, **kwargs)
```

Then add `HumanAnnotationsFeatureFlagMixin` as the first parent class to all views in schema_views.py, queue_views.py, and annotate_views.py. For example:

```python
from . import HumanAnnotationsFeatureFlagMixin

class AnnotationSchemaHome(HumanAnnotationsFeatureFlagMixin, LoginAndTeamRequiredMixin, TemplateView, PermissionRequiredMixin):
    ...
```

Apply this to all view classes.

**Step 4: Update tests to enable the flag**

All existing tests need the flag active. Add `@override_flag("human_annotations", active=True)` to the existing test classes/functions, or use a module-level fixture:

```python
# Add to each test file's fixtures
@pytest.fixture(autouse=True)
def enable_flag(settings):
    from waffle.testutils import override_flag
    with override_flag("human_annotations", active=True):
        yield
```

Alternatively, use `@pytest.mark.parametrize` or a simpler autouse fixture.

**Step 5: Run tests**

Run: `pytest apps/human_annotations/tests/ -v`
Expected: All tests PASS

**Step 6: Lint and commit**

Run: `ruff check apps/human_annotations/ --fix && ruff format apps/human_annotations/`

```bash
git add apps/human_annotations/
git commit -m "feat: gate human annotations behind waffle feature flag"
```

---

## Task 15: Navigation Integration

**Files:**
- Identify the sidebar/navigation template (look in `templates/` for nav patterns)
- Add links to Annotation Schemas and Annotation Queues in the team navigation
- Gate navigation links behind the feature flag

**Step 1: Find the navigation template**

Search for the sidebar template that includes links to other apps like "Tags", "Evaluations", etc. Look in `templates/web/` or `templates/generic/`.

**Step 2: Add navigation entries**

Add entries gated behind the feature flag:

```html
{% load waffle_tags %}
{% flag "human_annotations" %}
  <li><a href="{% url 'human_annotations:schema_home' team_slug %}">Annotation Schemas</a></li>
  <li><a href="{% url 'human_annotations:queue_home' team_slug %}">Annotation Queues</a></li>
{% endflag %}
```

**Step 3: Test navigation**

Manually verify or write a simple test that checks the navigation renders correctly.

**Step 4: Commit**

```bash
git add templates/
git commit -m "feat: add human annotations navigation links with feature flag"
```

---

## Task 16: Add "Start Annotating" Button to Queue Detail

**Files:**
- Modify: `templates/human_annotations/queue_detail.html`
- Modify: `templates/human_annotations/queue_detail.html`

**Context:** Add action buttons to the queue detail page: "Start Annotating" (links to annotate view), "Add Sessions", "Import CSV", "Export", "Manage Assignees".

**Step 1: Update the queue detail template**

Add an actions section to `templates/human_annotations/queue_detail.html` between the header and progress card:

```html
<!-- Actions -->
<div class="flex flex-wrap gap-2">
  <a href="{% url 'human_annotations:annotate_queue' team_slug=request.team.slug pk=object.pk %}"
     class="btn btn-primary btn-sm">Start Annotating</a>
  <a href="{% url 'human_annotations:queue_add_sessions' team_slug=request.team.slug pk=object.pk %}"
     class="btn btn-outline btn-sm">Add Sessions</a>
  <a href="{% url 'human_annotations:queue_import_csv' team_slug=request.team.slug pk=object.pk %}"
     class="btn btn-outline btn-sm">Import CSV</a>
  <a href="{% url 'human_annotations:queue_export' team_slug=request.team.slug pk=object.pk %}?format=csv"
     class="btn btn-outline btn-sm">Export CSV</a>
  <a href="{% url 'human_annotations:queue_export' team_slug=request.team.slug pk=object.pk %}?format=jsonl"
     class="btn btn-outline btn-sm">Export JSONL</a>
  <a href="{% url 'human_annotations:queue_manage_assignees' team_slug=request.team.slug pk=object.pk %}"
     class="btn btn-outline btn-sm">Manage Assignees</a>
</div>
```

**Step 2: Commit**

```bash
git add templates/human_annotations/
git commit -m "feat: add action buttons to queue detail page"
```

---

## Task 17: Annotation Data Validation

**Files:**
- Create: `apps/human_annotations/validation.py`
- Create: `apps/human_annotations/tests/test_validation.py`

**Context:** Add a validation utility that validates annotation data against the queue's schema using the Pydantic model generation from `apps/evaluations/utils.py:schema_to_pydantic_model`. This ensures submitted data matches the expected types and constraints.

**Step 1: Write the failing test**

```python
# apps/human_annotations/tests/test_validation.py
import pytest

from apps.human_annotations.validation import validate_annotation_data


def test_validate_valid_data():
    schema = {
        "score": {"type": "int", "description": "Score", "ge": 1, "le": 5},
        "category": {"type": "choice", "description": "Cat", "choices": ["good", "bad"]},
    }
    data = {"score": 3, "category": "good"}
    errors = validate_annotation_data(schema, data)
    assert errors == {}


def test_validate_invalid_score():
    schema = {
        "score": {"type": "int", "description": "Score", "ge": 1, "le": 5},
    }
    data = {"score": 10}
    errors = validate_annotation_data(schema, data)
    assert "score" in errors


def test_validate_missing_field():
    schema = {
        "score": {"type": "int", "description": "Score", "ge": 1, "le": 5},
    }
    data = {}
    errors = validate_annotation_data(schema, data)
    assert "score" in errors
```

**Step 2: Run test to verify it fails**

Run: `pytest apps/human_annotations/tests/test_validation.py -v`
Expected: FAIL

**Step 3: Write the validation utility**

```python
# apps/human_annotations/validation.py
from apps.evaluations.utils import schema_to_pydantic_model


def validate_annotation_data(schema_dict: dict, data: dict) -> dict[str, str]:
    """Validate annotation data against a schema. Returns dict of field_name -> error message."""
    try:
        model = schema_to_pydantic_model(schema_dict, "AnnotationValidation")
        model(**data)
        return {}
    except Exception as e:
        errors = {}
        if hasattr(e, "errors"):
            for error in e.errors():
                field = error["loc"][0] if error["loc"] else "unknown"
                errors[str(field)] = error["msg"]
        else:
            errors["__all__"] = str(e)
        return errors
```

**Step 4: Run tests**

Run: `pytest apps/human_annotations/tests/test_validation.py -v`
Expected: All tests PASS

**Step 5: Integrate validation into SubmitAnnotation view**

In `apps/human_annotations/views/annotate_views.py`, after the Django form validation passes, add Pydantic validation:

```python
from ..validation import validate_annotation_data

# In SubmitAnnotation.post, after form.is_valid():
schema_errors = validate_annotation_data(queue.schema.schema, form.cleaned_data)
if schema_errors:
    for field, error in schema_errors.items():
        form.add_error(field if field != "__all__" else None, error)
    # Re-render with errors...
```

**Step 6: Lint and commit**

Run: `ruff check apps/human_annotations/ --fix && ruff format apps/human_annotations/`

```bash
git add apps/human_annotations/
git commit -m "feat: add Pydantic schema validation for annotation submissions"
```

---

## Task 18: Final Integration Tests

**Files:**
- Create: `apps/human_annotations/tests/test_integration.py`

**Context:** End-to-end test covering the full workflow: create schema, create queue, add items, annotate items, verify completion status, export.

**Step 1: Write the integration test**

```python
# apps/human_annotations/tests/test_integration.py
import csv
import io

import pytest
from django.urls import reverse

from apps.human_annotations.models import (
    Annotation,
    AnnotationItem,
    AnnotationItemStatus,
    AnnotationQueue,
    AnnotationSchema,
    QueueStatus,
)
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def team():
    return TeamWithUsersFactory()


@pytest.fixture()
def logged_in_client(team, client):
    user = team.members.first()
    client.login(username=user.username, password="password")
    return client


@pytest.fixture(autouse=True)
def enable_flag():
    from waffle.testutils import override_flag
    with override_flag("human_annotations", active=True):
        yield


@pytest.mark.django_db()
def test_full_annotation_workflow(logged_in_client, team):
    user1 = team.members.first()
    user2 = team.members.last()

    # 1. Create schema
    schema = AnnotationSchema.objects.create(
        team=team,
        name="Quality Review",
        schema={
            "score": {"type": "int", "description": "Score 1-5", "ge": 1, "le": 5},
            "feedback": {"type": "string", "description": "Feedback"},
        },
    )

    # 2. Create queue with 2 reviews required
    queue = AnnotationQueue.objects.create(
        team=team,
        name="Q1 Audit",
        schema=schema,
        created_by=user1,
        num_reviews_required=2,
    )
    queue.assignees.add(user1, user2)

    # 3. Add items from sessions
    sessions = [ExperimentSessionFactory(team=team, chat__team=team) for _ in range(3)]
    for session in sessions:
        AnnotationItem.objects.create(
            queue=queue, team=team, item_type="session", session=session,
        )

    assert queue.items.count() == 3
    progress = queue.get_progress()
    assert progress["total"] == 3
    assert progress["completed"] == 0

    # 4. User1 annotates item 1
    item1 = queue.items.first()
    Annotation.objects.create(
        item=item1, team=team, reviewer=user1, data={"score": 4, "feedback": "Good"},
    )
    item1.refresh_from_db()
    assert item1.status == AnnotationItemStatus.IN_PROGRESS
    assert item1.review_count == 1

    # 5. User2 annotates item 1 -> item completes
    Annotation.objects.create(
        item=item1, team=team, reviewer=user2, data={"score": 5, "feedback": "Great"},
    )
    item1.refresh_from_db()
    assert item1.status == AnnotationItemStatus.COMPLETED
    assert item1.review_count == 2

    # 6. Verify progress
    progress = queue.get_progress()
    assert progress["completed"] == 1
    assert progress["total"] == 3

    # 7. Export CSV
    url = reverse("human_annotations:queue_export", kwargs={"team_slug": team.slug, "pk": queue.pk})
    response = logged_in_client.get(url, {"format": "csv"})
    assert response.status_code == 200
    content = response.content.decode()
    reader = csv.DictReader(io.StringIO(content))
    rows = list(reader)
    assert len(rows) == 2  # 2 annotations on item1
```

**Step 2: Run test**

Run: `pytest apps/human_annotations/tests/test_integration.py -v`
Expected: PASS

**Step 3: Run all tests**

Run: `pytest apps/human_annotations/tests/ -v`
Expected: All tests PASS

**Step 4: Lint and commit**

Run: `ruff check apps/human_annotations/ --fix && ruff format apps/human_annotations/`

```bash
git add apps/human_annotations/
git commit -m "test: add end-to-end integration test for annotation workflow"
```

---

## Summary

### Models Created
| Model | Purpose |
|-------|---------|
| `AnnotationSchema` | Defines annotation fields (reuses FieldDefinition) |
| `AnnotationQueue` | Groups items, tracks assignees and review requirements |
| `AnnotationItem` | Single item to annotate (session/message/external) |
| `Annotation` | Submitted review with validated data |

### Views Created
| View | Purpose |
|------|---------|
| Schema CRUD | Create/edit/delete annotation schemas |
| Queue CRUD | Create/edit/delete annotation queues |
| Queue Detail | Admin dashboard with progress and items |
| Add Sessions | Bulk add sessions to queue |
| Import CSV | Upload external data as items |
| Manage Assignees | Add/remove queue reviewers |
| Annotate | One-at-a-time annotation UI |
| Export | CSV/JSONL download of annotations |

---

## Phase 1.5: Aggregate Scores

This section adds aggregate score display to annotation queues, following the same pattern as the evaluation system's `EvaluationRunAggregate`.

### Architecture

The evaluation system uses:
- `EvaluationRunAggregate` model: stores per-evaluator aggregated results as JSON
- `apps/evaluations/aggregators.py`: pluggable aggregator classes (Mean, Median, Min, Max, StdDev for numeric; Distribution, Mode for categorical)
- `apps/evaluations/aggregation.py`: `compute_aggregates_for_run()` orchestrates aggregation
- Template display: card-based grid with numeric stats and categorical distributions

For annotations, we follow the same pattern but aggregate across all submitted annotations for a queue, grouped by schema field.

### Task A1: Add AnnotationQueueAggregate Model

**Files:**
- Modify: `apps/human_annotations/models.py`
- Create: `apps/human_annotations/migrations/0006_annotation_queue_aggregate.py`

**Implementation:**

```python
# In models.py, add:
class AnnotationQueueAggregate(BaseTeamModel):
    """Stores aggregated annotation results for a queue."""
    queue = models.OneToOneField(AnnotationQueue, on_delete=models.CASCADE, related_name="aggregate")
    aggregates = models.JSONField(default=dict, help_text="Aggregated stats per schema field")
    computed_at = models.DateTimeField(auto_now=True)
```

**Step 1: Write failing test**

```python
# tests/test_models.py
def test_create_queue_aggregate(team):
    # Create queue with schema, add items + annotations
    # Call compute function
    # Assert aggregate object created with expected stats
```

**Step 2: Add model + migration**

```bash
python manage.py makemigrations human_annotations
```

**Step 3: Commit**

```bash
git commit -m "feat: add AnnotationQueueAggregate model"
```

### Task A2: Add Aggregation Logic

**Files:**
- Create: `apps/human_annotations/aggregation.py`

**Implementation:**

Reuse the existing aggregator classes from `apps/evaluations/aggregators.py` directly (they are generic and not evaluator-specific).

```python
# apps/human_annotations/aggregation.py
from collections import defaultdict

from apps.evaluations.aggregators import aggregate_field
from apps.human_annotations.models import AnnotationQueueAggregate, AnnotationStatus


def compute_aggregates_for_queue(queue) -> AnnotationQueueAggregate:
    """
    Compute and store aggregates for all submitted annotations in a queue.
    Groups by schema field and applies numeric/categorical aggregators.
    """
    annotations = queue.items.prefetch_related("annotations").all()

    field_values = defaultdict(list)
    for item in annotations:
        for ann in item.annotations.filter(status=AnnotationStatus.SUBMITTED):
            for field_name, value in ann.data.items():
                if value is not None:
                    field_values[field_name].append(value)

    agg_data = {
        field_name: aggregate_field(values)
        for field_name, values in field_values.items()
    }

    obj, _ = AnnotationQueueAggregate.objects.update_or_create(
        queue=queue,
        defaults={"aggregates": agg_data, "team": queue.team},
    )
    return obj
```

**Step 1: Write failing tests**

```python
# tests/test_aggregation.py
def test_compute_aggregates_numeric():
    # Queue with int schema field, 3 annotations with scores 3, 4, 5
    # Assert mean=4.0, median=4.0, min=3, max=5

def test_compute_aggregates_categorical():
    # Queue with choice schema field, annotations with "good", "good", "bad"
    # Assert mode="good", distribution={"good": 66.7, "bad": 33.3}

def test_compute_aggregates_empty_queue():
    # Queue with no annotations
    # Assert empty aggregates dict
```

**Step 2: Implement aggregation.py**

**Step 3: Run tests, verify pass**

**Step 4: Commit**

```bash
git commit -m "feat: add annotation aggregation logic reusing eval aggregators"
```

### Task A3: Display Aggregates on Queue Detail Page

**Files:**
- Modify: `apps/human_annotations/views/queue_views.py` (AnnotationQueueDetail.get_context_data)
- Modify: `templates/human_annotations/queue_detail.html`

**Implementation:**

In the view, compute (or fetch cached) aggregates:

```python
# In AnnotationQueueDetail.get_context_data:
from apps.human_annotations.aggregation import compute_aggregates_for_queue

aggregate = compute_aggregates_for_queue(queue)
context["aggregates"] = aggregate.aggregates
```

In the template, add an aggregates card following the evaluation pattern:

```html
{% if aggregates %}
<div class="card bg-base-100 shadow-sm">
  <div class="card-body">
    <h3 class="card-title text-sm">Aggregate Scores</h3>
    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 mt-2">
      {% for field_name, stats in aggregates.items %}
        <div class="border border-base-300 rounded-lg p-3">
          <div class="flex justify-between items-center mb-2">
            <span class="font-medium">{{ field_name }}</span>
            <span class="text-xs text-base-content/60">n={{ stats.count }}</span>
          </div>
          {% if stats.type == "numeric" %}
            <div class="grid grid-cols-2 gap-1 text-sm">
              <div>mean: <span class="font-mono">{{ stats.mean }}</span></div>
              <div>median: <span class="font-mono">{{ stats.median }}</span></div>
              <div>min: <span class="font-mono">{{ stats.min }}</span></div>
              <div>max: <span class="font-mono">{{ stats.max }}</span></div>
            </div>
          {% elif stats.type == "categorical" %}
            <div class="text-sm mb-1">mode: <span class="font-medium">{{ stats.mode }}</span></div>
            <div class="flex flex-wrap gap-1">
              {% for value, pct in stats.distribution.items %}
                <span class="badge badge-sm badge-outline">{{ value }}: {{ pct }}%</span>
              {% endfor %}
            </div>
          {% endif %}
        </div>
      {% endfor %}
    </div>
  </div>
</div>
{% endif %}
```

**Step 1: Write view test**

```python
def test_queue_detail_shows_aggregates(client, team_with_users, queue, user):
    # Create items + annotations
    # GET queue detail
    # Assert "Aggregate Scores" in response content
    # Assert aggregate values are displayed
```

**Step 2: Update view + template**

**Step 3: Run tests, verify pass**

**Step 4: Commit**

```bash
git commit -m "feat: display aggregate scores on queue detail page"
```

### Task A4: Auto-Recompute Aggregates on Annotation Submit

**Files:**
- Modify: `apps/human_annotations/models.py` (Annotation.save)

**Implementation:**

After an annotation is submitted, trigger aggregate recomputation:

```python
# In Annotation.save():
def save(self, *args, **kwargs):
    is_new = self._state.adding
    super().save(*args, **kwargs)
    if is_new and self.status == AnnotationStatus.SUBMITTED:
        self._update_item_review_count()
        # Recompute aggregates
        from apps.human_annotations.aggregation import compute_aggregates_for_queue
        compute_aggregates_for_queue(self.item.queue)
```

For large queues, this could be moved to a Celery task, but for Phase 1.5 inline computation is sufficient since aggregation is lightweight (just iterating submitted annotation data).

**Step 1: Write test**

```python
def test_aggregate_updates_on_new_annotation():
    # Create queue + item
    # Submit annotation
    # Assert aggregate exists and has correct values
    # Submit second annotation
    # Assert aggregate updated
```

**Step 2: Implement**

**Step 3: Run tests, verify pass**

**Step 4: Commit**

```bash
git commit -m "feat: auto-recompute aggregates on annotation submit"
```

---

### Phase 2+ (Future Work)
These features from the ticket are deferred to a separate plan:
- **Item 7**: Gold standard items, inter-annotator agreement (Cohen's kappa)
- **Item 8**: Integration with evaluations (create queues from eval results, convert to datasets)
- **Item 9**: Session UI integration (add to queue from session detail)
- **Item 10**: Queue automation (auto-add based on criteria, events integration)
- **Item 11**: Notification integration (new items, status changes, flagged items)
