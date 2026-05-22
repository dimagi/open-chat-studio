# Authoritative Annotation Flag Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `is_authoritative` flag to `apps.human_annotations.models.Annotation` so queue admins can resolve multi-reviewer conflicts; drive aggregation and item completion from that flag.

**Architecture:** Boolean field on `Annotation` with partial-unique constraint enforcing at-most-one-per-item. New `AWAITING_RESOLUTION` status on `AnnotationItem` for multi-reviewer items whose reviews are all in but lack an authoritative pick. `Annotation.save` auto-marks for single-reviewer queues. New admin-only `SetAuthoritative` POST endpoint. `compute_aggregates_for_queue` prefers authoritative annotations per item, falls back to all submitted.

**Tech Stack:** Django 5.x, pytest, factory_boy, HTMX, Tailwind/DaisyUI.

**Spec:** `docs/superpowers/specs/2026-05-18-authoritative-annotation-design.md`

---

## Conventions

- All Python edits go through ruff: `uv run ruff check <path> --fix` and `uv run ruff format <path>` after each task.
- Tests use `uv run pytest <path> -v`.
- Commit with the existing co-author footer style; do not push.
- Use absolute paths from repo root in this plan. Repo root: `/home/skelly/src/open-chat-studio.sk-authoratitive-annotation`.

---

### Task 1: Schema migration — fields, constraint, new status value

**Files:**
- Modify: `apps/human_annotations/models.py`
- Create: `apps/human_annotations/migrations/0003_authoritative_annotation_fields.py`
- Modify: `apps/human_annotations/tests/test_models.py`

- [ ] **Step 1: Add the failing constraint test**

Append to `apps/human_annotations/tests/test_models.py`:

```python
@pytest.mark.django_db()
def test_only_one_authoritative_annotation_per_item(team):
    from django.contrib.auth import get_user_model

    User = get_user_model()
    user1 = team.members.first()
    user2 = User.objects.create(username="reviewer2", email="r2@example.com")
    team.members.add(user2)
    queue = AnnotationQueue.objects.create(
        team=team, name="Q", schema={}, created_by=user1, num_reviews_required=2
    )
    session = ExperimentSessionFactory.create(team=team, chat__team=team)
    item = AnnotationItem.objects.create(
        queue=queue, team=team, item_type=AnnotationItemType.SESSION, session=session
    )
    Annotation.objects.create(item=item, team=team, reviewer=user1, data={}, is_authoritative=True)
    with pytest.raises(django.db.IntegrityError):
        Annotation.objects.create(item=item, team=team, reviewer=user2, data={}, is_authoritative=True)


@pytest.mark.django_db()
def test_authoritative_constraint_allows_one_per_item_across_different_items(team):
    user = team.members.first()
    queue = AnnotationQueue.objects.create(
        team=team, name="Q", schema={}, created_by=user, num_reviews_required=1
    )
    session1 = ExperimentSessionFactory.create(team=team, chat__team=team)
    session2 = ExperimentSessionFactory.create(team=team, chat__team=team)
    item1 = AnnotationItem.objects.create(
        queue=queue, team=team, item_type=AnnotationItemType.SESSION, session=session1
    )
    item2 = AnnotationItem.objects.create(
        queue=queue, team=team, item_type=AnnotationItemType.SESSION, session=session2
    )
    # Both items can have an authoritative annotation simultaneously.
    Annotation.objects.create(item=item1, team=team, reviewer=user, data={}, is_authoritative=True)
    Annotation.objects.create(item=item2, team=team, reviewer=user, data={}, is_authoritative=True)
```

- [ ] **Step 2: Run the failing tests**

```bash
uv run pytest apps/human_annotations/tests/test_models.py::test_only_one_authoritative_annotation_per_item apps/human_annotations/tests/test_models.py::test_authoritative_constraint_allows_one_per_item_across_different_items -v
```

Expected: FAIL — `Annotation` has no `is_authoritative` field (TypeError on the create call).

- [ ] **Step 3: Add the model fields and constraint**

Edit `apps/human_annotations/models.py`. In the `AnnotationItemStatus` class (currently around line 24), add the new status. Replace:

```python
class AnnotationItemStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    IN_PROGRESS = "in_progress", "In Progress"
    COMPLETED = "completed", "Completed"
    FLAGGED = "flagged", "Flagged"
```

with:

```python
class AnnotationItemStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    IN_PROGRESS = "in_progress", "In Progress"
    AWAITING_RESOLUTION = "awaiting_resolution", "Awaiting resolution"
    COMPLETED = "completed", "Completed"
    FLAGGED = "flagged", "Flagged"
```

In the `Annotation` class (currently around line 203), add three fields below the `data` field, and a `UniqueConstraint` in the `Meta` class. The full updated `Annotation` model:

```python
class Annotation(BaseTeamModel):
    """A single review/annotation submitted by a reviewer for an item."""

    item = models.ForeignKey(AnnotationItem, on_delete=models.CASCADE, related_name="annotations")
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="annotations",
    )
    data = SanitizedJSONField(default=dict, help_text="Annotation data matching the queue's schema")
    status = models.CharField(
        max_length=20,
        choices=AnnotationStatus.choices,
        default=AnnotationStatus.SUBMITTED,
    )
    is_authoritative = models.BooleanField(default=False)
    authoritative_set_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="authoritative_annotations_set",
    )
    authoritative_set_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("item", "reviewer")
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["item"],
                condition=models.Q(is_authoritative=True),
                name="one_authoritative_annotation_per_item",
            ),
        ]
```

- [ ] **Step 4: Generate the migration**

```bash
uv run python manage.py makemigrations human_annotations -n authoritative_annotation_fields
```

Expected: writes `apps/human_annotations/migrations/0003_authoritative_annotation_fields.py` containing `AddField` for the three new fields, `AlterField` for `AnnotationItem.status` (new choice), and `AddConstraint` for the partial unique index.

- [ ] **Step 5: Run the constraint tests again**

```bash
uv run pytest apps/human_annotations/tests/test_models.py::test_only_one_authoritative_annotation_per_item apps/human_annotations/tests/test_models.py::test_authoritative_constraint_allows_one_per_item_across_different_items -v
```

Expected: PASS.

- [ ] **Step 6: Lint and format**

```bash
uv run ruff check apps/human_annotations/models.py apps/human_annotations/tests/test_models.py --fix
uv run ruff format apps/human_annotations/models.py apps/human_annotations/tests/test_models.py
```

- [ ] **Step 7: Commit**

```bash
git add apps/human_annotations/models.py apps/human_annotations/migrations/0003_authoritative_annotation_fields.py apps/human_annotations/tests/test_models.py
git commit -m "feat(human_annotations): add is_authoritative field & AWAITING_RESOLUTION status"
```

---

### Task 2: Auto-mark single-reviewer queue on submission

**Files:**
- Modify: `apps/human_annotations/models.py`
- Create: `apps/human_annotations/tests/test_authoritative.py`

- [ ] **Step 1: Create the new test file with failing tests**

Create `apps/human_annotations/tests/test_authoritative.py`:

```python
import pytest
from django.contrib.auth import get_user_model

from apps.human_annotations.models import (
    Annotation,
    AnnotationItem,
    AnnotationItemStatus,
    AnnotationItemType,
    AnnotationQueue,
    AnnotationStatus,
)
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.team import TeamWithUsersFactory

User = get_user_model()


@pytest.fixture()
def team():
    return TeamWithUsersFactory.create()


@pytest.fixture()
def second_user(team):
    from apps.utils.factories.team import MembershipFactory

    user = User.objects.create(username="reviewer2", email="r2@example.com")
    MembershipFactory.create(team=team, user=user)
    return user


def _make_queue(team, num_reviews_required=1):
    return AnnotationQueue.objects.create(
        team=team,
        name=f"Q-{num_reviews_required}r",
        schema={"score": {"type": "int", "description": "Score", "ge": 1, "le": 5}},
        created_by=team.members.first(),
        num_reviews_required=num_reviews_required,
    )


def _make_item(queue):
    session = ExperimentSessionFactory.create(team=queue.team, chat__team=queue.team)
    return AnnotationItem.objects.create(
        queue=queue,
        team=queue.team,
        item_type=AnnotationItemType.SESSION,
        session=session,
    )


@pytest.mark.django_db()
def test_single_reviewer_first_submission_auto_marks_authoritative(team):
    queue = _make_queue(team, num_reviews_required=1)
    item = _make_item(queue)
    user = team.members.first()

    ann = Annotation.objects.create(
        item=item, team=team, reviewer=user, data={"score": 5}, status=AnnotationStatus.SUBMITTED
    )

    ann.refresh_from_db()
    assert ann.is_authoritative is True
    assert ann.authoritative_set_by is None
    assert ann.authoritative_set_at is not None


@pytest.mark.django_db()
def test_single_reviewer_second_submission_does_not_auto_mark(team, second_user):
    queue = _make_queue(team, num_reviews_required=1)
    item = _make_item(queue)
    user = team.members.first()

    Annotation.objects.create(
        item=item, team=team, reviewer=user, data={"score": 5}, status=AnnotationStatus.SUBMITTED
    )
    second = Annotation.objects.create(
        item=item, team=team, reviewer=second_user, data={"score": 4}, status=AnnotationStatus.SUBMITTED
    )

    assert second.is_authoritative is False
    assert second.authoritative_set_by is None
    assert second.authoritative_set_at is None


@pytest.mark.django_db()
def test_multi_reviewer_submission_does_not_auto_mark(team):
    queue = _make_queue(team, num_reviews_required=2)
    item = _make_item(queue)
    user = team.members.first()

    ann = Annotation.objects.create(
        item=item, team=team, reviewer=user, data={"score": 5}, status=AnnotationStatus.SUBMITTED
    )

    assert ann.is_authoritative is False
    assert ann.authoritative_set_at is None
```

- [ ] **Step 2: Run the failing tests**

```bash
uv run pytest apps/human_annotations/tests/test_authoritative.py -v
```

Expected: FAIL — first test asserts `is_authoritative is True` but no auto-mark logic exists.

- [ ] **Step 3: Implement auto-mark in `Annotation.save`**

Edit `apps/human_annotations/models.py`. Add `timezone` to imports at the top:

```python
from django.utils import timezone
```

Replace the `save` method on `Annotation` (currently around line 226-230):

```python
def save(self, *args, **kwargs):
    is_new = self._state.adding
    if is_new and self.status == AnnotationStatus.SUBMITTED:
        self._maybe_auto_mark_authoritative()
    super().save(*args, **kwargs)
    if is_new and self.status == AnnotationStatus.SUBMITTED:
        self._update_item_review_count()

def _maybe_auto_mark_authoritative(self):
    """For single-reviewer queues, auto-mark the first submission as authoritative.
    Skips when another authoritative annotation already exists on the item (handles
    over-budget submissions and avoids violating the partial unique constraint)."""
    queue = self.item.queue
    if queue.num_reviews_required != 1:
        return
    if Annotation.objects.filter(item=self.item, is_authoritative=True).exists():
        return
    self.is_authoritative = True
    self.authoritative_set_by = None
    self.authoritative_set_at = timezone.now()
```

- [ ] **Step 4: Run the tests**

```bash
uv run pytest apps/human_annotations/tests/test_authoritative.py -v
```

Expected: PASS (all three).

- [ ] **Step 5: Lint and format**

```bash
uv run ruff check apps/human_annotations/models.py apps/human_annotations/tests/test_authoritative.py --fix
uv run ruff format apps/human_annotations/models.py apps/human_annotations/tests/test_authoritative.py
```

- [ ] **Step 6: Commit**

```bash
git add apps/human_annotations/models.py apps/human_annotations/tests/test_authoritative.py
git commit -m "feat(human_annotations): auto-mark authoritative for single-reviewer queues"
```

---

### Task 3: Rewrite `AnnotationItem.update_status` with new transitions

**Files:**
- Modify: `apps/human_annotations/models.py`
- Modify: `apps/human_annotations/tests/test_authoritative.py`

- [ ] **Step 1: Add the failing status-transition tests**

Append to `apps/human_annotations/tests/test_authoritative.py`:

```python
@pytest.mark.django_db()
def test_single_reviewer_completed_after_submission(team):
    queue = _make_queue(team, num_reviews_required=1)
    item = _make_item(queue)
    user = team.members.first()

    Annotation.objects.create(
        item=item, team=team, reviewer=user, data={"score": 5}, status=AnnotationStatus.SUBMITTED
    )

    item.refresh_from_db()
    assert item.status == AnnotationItemStatus.COMPLETED


@pytest.mark.django_db()
def test_multi_reviewer_in_progress_then_awaiting_resolution(team, second_user):
    queue = _make_queue(team, num_reviews_required=2)
    item = _make_item(queue)
    user1 = team.members.first()

    Annotation.objects.create(
        item=item, team=team, reviewer=user1, data={"score": 5}, status=AnnotationStatus.SUBMITTED
    )
    item.refresh_from_db()
    assert item.status == AnnotationItemStatus.IN_PROGRESS

    Annotation.objects.create(
        item=item, team=team, reviewer=second_user, data={"score": 3}, status=AnnotationStatus.SUBMITTED
    )
    item.refresh_from_db()
    assert item.status == AnnotationItemStatus.AWAITING_RESOLUTION


@pytest.mark.django_db()
def test_multi_reviewer_completed_when_authoritative_set(team, second_user):
    queue = _make_queue(team, num_reviews_required=2)
    item = _make_item(queue)
    user1 = team.members.first()

    ann1 = Annotation.objects.create(
        item=item, team=team, reviewer=user1, data={"score": 5}, status=AnnotationStatus.SUBMITTED
    )
    Annotation.objects.create(
        item=item, team=team, reviewer=second_user, data={"score": 3}, status=AnnotationStatus.SUBMITTED
    )

    ann1.is_authoritative = True
    ann1.save(update_fields=["is_authoritative"])
    item.update_status()

    item.refresh_from_db()
    assert item.status == AnnotationItemStatus.COMPLETED


@pytest.mark.django_db()
def test_flagged_status_preserved(team):
    queue = _make_queue(team, num_reviews_required=2)
    item = _make_item(queue)
    item.status = AnnotationItemStatus.FLAGGED
    item.save(update_fields=["status"])

    item.update_status()

    item.refresh_from_db()
    assert item.status == AnnotationItemStatus.FLAGGED
```

- [ ] **Step 2: Run the failing tests**

```bash
uv run pytest apps/human_annotations/tests/test_authoritative.py -v
```

Expected: `test_multi_reviewer_in_progress_then_awaiting_resolution` and `test_multi_reviewer_completed_when_authoritative_set` FAIL — current `update_status` marks `review_count >= num_reviews_required` as COMPLETED regardless of authoritative state.

- [ ] **Step 3: Rewrite `update_status`**

In `apps/human_annotations/models.py`, replace the `update_status` method on `AnnotationItem` (currently around line 183-195) with:

```python
def update_status(self, save=True):
    """Update item status based on review count, authoritative flag, and queue requirement.
    Preserves FLAGGED status — only explicit unflagging clears it."""
    if self.status == AnnotationItemStatus.FLAGGED:
        return

    required = self.queue.num_reviews_required
    has_authoritative = self.annotations.filter(
        status=AnnotationStatus.SUBMITTED, is_authoritative=True
    ).exists()

    if self.review_count == 0:
        self.status = AnnotationItemStatus.PENDING
    elif self.review_count < required:
        self.status = AnnotationItemStatus.IN_PROGRESS
    elif required == 1 or has_authoritative:
        self.status = AnnotationItemStatus.COMPLETED
    else:
        self.status = AnnotationItemStatus.AWAITING_RESOLUTION

    if save:
        self.save(update_fields=["status"])
```

- [ ] **Step 4: Run the tests**

```bash
uv run pytest apps/human_annotations/tests/test_authoritative.py -v
```

Expected: PASS (all seven so far).

- [ ] **Step 5: Run the existing model and view tests to catch regressions**

```bash
uv run pytest apps/human_annotations/tests/ -v
```

Expected: PASS. If any existing test fails because of the new AWAITING_RESOLUTION status, inspect — it likely needs to be updated to reflect that multi-reviewer items no longer become COMPLETED without an authoritative pick. Fix only if the failure is due to the new behavior; otherwise report it.

- [ ] **Step 6: Lint and format**

```bash
uv run ruff check apps/human_annotations/models.py apps/human_annotations/tests/test_authoritative.py --fix
uv run ruff format apps/human_annotations/models.py apps/human_annotations/tests/test_authoritative.py
```

- [ ] **Step 7: Commit**

```bash
git add apps/human_annotations/models.py apps/human_annotations/tests/test_authoritative.py
git commit -m "feat(human_annotations): AWAITING_RESOLUTION status for unresolved multi-reviewer items"
```

---

### Task 4: `SetAuthoritative` view + URL

**Files:**
- Modify: `apps/human_annotations/views/annotate_views.py`
- Modify: `apps/human_annotations/urls.py`
- Modify: `apps/human_annotations/tests/test_authoritative.py`
- Create: `templates/human_annotations/partials/annotation_list.html`

- [ ] **Step 1: Add the failing view tests**

Append to `apps/human_annotations/tests/test_authoritative.py`:

```python
from django.test import Client
from django.urls import reverse

from apps.teams.backends import ANNOTATION_REVIEWER_GROUP
from apps.utils.factories.team import MembershipFactory


@pytest.fixture()
def admin_client(team):
    """Team owner — has change_annotationqueue."""
    user = team.members.first()
    c = Client()
    c.force_login(user)
    return c


@pytest.fixture()
def reviewer_user(team):
    """A user with only the ANNOTATION_REVIEWER_GROUP (no change_annotationqueue)."""
    user = User.objects.create(username="reviewer-only", email="ro@example.com")
    membership = MembershipFactory.create(team=team, user=user)
    from django.contrib.auth.models import Group

    membership.groups.set([Group.objects.get(name=ANNOTATION_REVIEWER_GROUP)])
    return user


@pytest.fixture()
def reviewer_client(reviewer_user):
    c = Client()
    c.force_login(reviewer_user)
    return c


def _set_authoritative_url(team, queue, item, annotation):
    return reverse(
        "human_annotations:set_authoritative",
        args=[team.slug, queue.pk, item.pk, annotation.pk],
    )


@pytest.mark.django_db()
def test_set_authoritative_as_admin(admin_client, team, second_user):
    queue = _make_queue(team, num_reviews_required=2)
    item = _make_item(queue)
    user1 = team.members.first()
    ann1 = Annotation.objects.create(
        item=item, team=team, reviewer=user1, data={"score": 5}, status=AnnotationStatus.SUBMITTED
    )
    Annotation.objects.create(
        item=item, team=team, reviewer=second_user, data={"score": 3}, status=AnnotationStatus.SUBMITTED
    )

    response = admin_client.post(_set_authoritative_url(team, queue, item, ann1), {"value": "true"})

    assert response.status_code in (200, 302)
    ann1.refresh_from_db()
    item.refresh_from_db()
    assert ann1.is_authoritative is True
    assert ann1.authoritative_set_by_id == user1.id
    assert ann1.authoritative_set_at is not None
    assert item.status == AnnotationItemStatus.COMPLETED


@pytest.mark.django_db()
def test_set_authoritative_clears_other_annotations(admin_client, team, second_user):
    queue = _make_queue(team, num_reviews_required=2)
    item = _make_item(queue)
    user1 = team.members.first()
    ann1 = Annotation.objects.create(
        item=item, team=team, reviewer=user1, data={}, status=AnnotationStatus.SUBMITTED
    )
    ann2 = Annotation.objects.create(
        item=item, team=team, reviewer=second_user, data={}, status=AnnotationStatus.SUBMITTED
    )

    admin_client.post(_set_authoritative_url(team, queue, item, ann1), {"value": "true"})
    admin_client.post(_set_authoritative_url(team, queue, item, ann2), {"value": "true"})

    ann1.refresh_from_db()
    ann2.refresh_from_db()
    assert ann1.is_authoritative is False
    assert ann1.authoritative_set_by_id is None
    assert ann1.authoritative_set_at is None
    assert ann2.is_authoritative is True


@pytest.mark.django_db()
def test_set_authoritative_value_false_clears(admin_client, team, second_user):
    queue = _make_queue(team, num_reviews_required=2)
    item = _make_item(queue)
    user1 = team.members.first()
    ann1 = Annotation.objects.create(
        item=item, team=team, reviewer=user1, data={}, status=AnnotationStatus.SUBMITTED
    )
    Annotation.objects.create(
        item=item, team=team, reviewer=second_user, data={}, status=AnnotationStatus.SUBMITTED
    )
    admin_client.post(_set_authoritative_url(team, queue, item, ann1), {"value": "true"})

    admin_client.post(_set_authoritative_url(team, queue, item, ann1), {"value": "false"})

    ann1.refresh_from_db()
    item.refresh_from_db()
    assert ann1.is_authoritative is False
    assert ann1.authoritative_set_at is None
    assert item.status == AnnotationItemStatus.AWAITING_RESOLUTION


@pytest.mark.django_db()
def test_set_authoritative_forbidden_for_reviewer(reviewer_client, team, second_user):
    queue = _make_queue(team, num_reviews_required=2)
    item = _make_item(queue)
    user1 = team.members.first()
    ann1 = Annotation.objects.create(
        item=item, team=team, reviewer=user1, data={}, status=AnnotationStatus.SUBMITTED
    )

    response = reviewer_client.post(_set_authoritative_url(team, queue, item, ann1), {"value": "true"})

    assert response.status_code == 403
    ann1.refresh_from_db()
    assert ann1.is_authoritative is False


@pytest.mark.django_db()
def test_set_authoritative_cross_team_404(admin_client, team):
    other_team = TeamWithUsersFactory.create()
    queue = _make_queue(other_team, num_reviews_required=2)
    item = _make_item(queue)
    user = other_team.members.first()
    ann = Annotation.objects.create(
        item=item, team=other_team, reviewer=user, data={}, status=AnnotationStatus.SUBMITTED
    )

    # Posting via the admin team's slug for a different team's queue should 404.
    url = reverse(
        "human_annotations:set_authoritative",
        args=[team.slug, queue.pk, item.pk, ann.pk],
    )
    response = admin_client.post(url, {"value": "true"})

    assert response.status_code == 404


@pytest.mark.django_db()
def test_set_authoritative_pre_mark_before_all_reviews(admin_client, team, second_user):
    queue = _make_queue(team, num_reviews_required=2)
    item = _make_item(queue)
    user1 = team.members.first()
    ann1 = Annotation.objects.create(
        item=item, team=team, reviewer=user1, data={}, status=AnnotationStatus.SUBMITTED
    )

    admin_client.post(_set_authoritative_url(team, queue, item, ann1), {"value": "true"})

    ann1.refresh_from_db()
    item.refresh_from_db()
    assert ann1.is_authoritative is True
    # Only one of two reviews submitted, so status should remain IN_PROGRESS.
    assert item.status == AnnotationItemStatus.IN_PROGRESS

    # When the second review arrives, item should now be COMPLETED (authoritative already set).
    Annotation.objects.create(
        item=item, team=team, reviewer=second_user, data={}, status=AnnotationStatus.SUBMITTED
    )
    item.refresh_from_db()
    assert item.status == AnnotationItemStatus.COMPLETED


@pytest.mark.django_db()
def test_set_authoritative_htmx_returns_partial(admin_client, team, second_user):
    queue = _make_queue(team, num_reviews_required=2)
    item = _make_item(queue)
    user1 = team.members.first()
    ann1 = Annotation.objects.create(
        item=item, team=team, reviewer=user1, data={}, status=AnnotationStatus.SUBMITTED
    )

    response = admin_client.post(
        _set_authoritative_url(team, queue, item, ann1),
        {"value": "true"},
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 200
    assert b"Authoritative" in response.content
```

- [ ] **Step 2: Run the failing tests**

```bash
uv run pytest apps/human_annotations/tests/test_authoritative.py -v
```

Expected: FAIL — `NoReverseMatch: 'set_authoritative'`.

- [ ] **Step 3: Add the URL route**

Edit `apps/human_annotations/urls.py`. After the `edit_annotation` route, add:

```python
path(
    "queue/<int:pk>/item/<int:item_pk>/annotation/<int:annotation_pk>/authoritative/",
    annotate_views.SetAuthoritative.as_view(),
    name="set_authoritative",
),
```

- [ ] **Step 4: Implement `SetAuthoritative` view**

Edit `apps/human_annotations/views/annotate_views.py`. Add to imports:

```python
from django.utils import timezone
```

Append the new view class at the end of the file:

```python
class SetAuthoritative(LoginAndTeamRequiredMixin, PermissionRequiredMixin, View):
    """Queue-admin endpoint to mark/unmark an annotation as authoritative.
    Enforces at-most-one-per-item at the application layer too (clears the flag
    on any sibling annotation before setting) so we never trip the partial unique constraint."""

    permission_required = "human_annotations.change_annotationqueue"

    def post(self, request, team_slug: str, pk: int, item_pk: int, annotation_pk: int):
        queue = get_object_or_404(AnnotationQueue, id=pk, team=request.team)
        annotation = get_object_or_404(
            Annotation.objects.select_related("item__queue"),
            id=annotation_pk,
            item_id=item_pk,
            item__queue=queue,
        )
        value = request.POST.get("value", "false").lower() == "true"

        with transaction.atomic():
            item = AnnotationItem.objects.select_for_update().get(pk=item_pk)
            if value:
                Annotation.objects.filter(item=item, is_authoritative=True).exclude(pk=annotation.pk).update(
                    is_authoritative=False,
                    authoritative_set_by=None,
                    authoritative_set_at=None,
                )
                annotation.is_authoritative = True
                annotation.authoritative_set_by = request.user
                annotation.authoritative_set_at = timezone.now()
            else:
                annotation.is_authoritative = False
                annotation.authoritative_set_by = None
                annotation.authoritative_set_at = None
            annotation.save(
                update_fields=["is_authoritative", "authoritative_set_by", "authoritative_set_at", "updated_at"]
            )
            item.update_status()

        annotation.recompute_queue_aggregates(queue)

        if request.headers.get("HX-Request"):
            annotations = _build_annotations_context(item, request.user, queue)
            return render(
                request,
                "human_annotations/partials/annotation_list.html",
                {"queue": queue, "item": item, "annotations": annotations},
            )
        return redirect("human_annotations:annotate_item", team_slug=team_slug, pk=pk, item_pk=item_pk)
```

Also create the helper `_build_annotations_context` near the top of the file (after the existing helpers around line 80):

```python
def _build_annotations_context(item, user, queue):
    """Build the annotations list for display on the annotate page."""
    schema_fields = list(queue.schema.keys())
    can_set_authoritative = user.has_perm("human_annotations.change_annotationqueue")
    return [
        {
            "annotation_id": ann.id,
            "reviewer": ann.reviewer,
            "created_at": ann.created_at,
            "fields": [(name, ann.data.get(name, "")) for name in schema_fields],
            "can_edit": ann.reviewer_id == user.id,
            "is_authoritative": ann.is_authoritative,
            "authoritative_set_by": ann.authoritative_set_by,
            "authoritative_set_at": ann.authoritative_set_at,
            "can_set_authoritative": can_set_authoritative,
        }
        for ann in item.annotations.filter(status=AnnotationStatus.SUBMITTED)
        .select_related("reviewer", "authoritative_set_by")
        .order_by("created_at")
    ]
```

Update `AnnotateItem.get` (currently around line 128) to use this helper. Replace its `annotations = [...]` block (the comprehension that builds annotation dicts) with:

```python
annotations = _build_annotations_context(item, request.user, queue)
```

- [ ] **Step 5: Create the partial template**

Create `templates/human_annotations/partials/annotation_list.html` with the body of the existing annotations list. Initially this is the same markup the inline annotation card uses today. Content:

```django
<div class="card bg-base-100 shadow-sm">
  <div class="card-body">
    <h3 class="card-title text-sm">Annotations ({{ annotations|length }})</h3>
    <div class="flex flex-col gap-3">
      {% for ann in annotations %}
        <div class="border rounded-lg p-3 {% if ann.is_authoritative %}border-primary{% endif %}">
          <div class="flex justify-between items-center mb-2">
            <div class="flex items-center gap-2">
              <span class="font-medium text-sm">{{ ann.reviewer.get_full_name|default:ann.reviewer.username }}</span>
              {% if ann.is_authoritative %}
                <span class="badge badge-primary badge-sm gap-1"
                      title="{% if ann.authoritative_set_by %}Marked authoritative by {{ ann.authoritative_set_by.get_full_name|default:ann.authoritative_set_by.username }} on {{ ann.authoritative_set_at|date:'DATETIME_FORMAT' }}{% else %}Auto-marked as authoritative on {{ ann.authoritative_set_at|date:'DATETIME_FORMAT' }}{% endif %}">
                  <i class="fa-solid fa-star"></i> Authoritative
                </span>
              {% endif %}
            </div>
            <div class="flex items-center gap-2">
              <span class="text-xs text-gray-500">{{ ann.created_at|date:'DATETIME_FORMAT' }}</span>
              {% if ann.can_edit %}
                <a href="{% url 'human_annotations:edit_annotation' request.team.slug queue.pk item.pk ann.annotation_id %}"
                   class="btn btn-xs btn-ghost"
                   title="Edit annotation">
                  <i class="fa-solid fa-pencil"></i> Edit
                </a>
              {% endif %}
              {% if ann.can_set_authoritative %}
                {% if ann.is_authoritative %}
                  <button type="button"
                          class="btn btn-xs btn-ghost"
                          hx-post="{% url 'human_annotations:set_authoritative' request.team.slug queue.pk item.pk ann.annotation_id %}"
                          hx-vals='{"value":"false"}'
                          hx-target="#annotation-list"
                          hx-swap="outerHTML"
                          hx-headers='{"X-CSRFToken":"{{ csrf_token }}"}'>
                    <i class="fa-solid fa-star-half-stroke"></i> Clear authoritative
                  </button>
                {% else %}
                  <button type="button"
                          class="btn btn-xs btn-outline btn-primary"
                          hx-post="{% url 'human_annotations:set_authoritative' request.team.slug queue.pk item.pk ann.annotation_id %}"
                          hx-vals='{"value":"true"}'
                          hx-target="#annotation-list"
                          hx-swap="outerHTML"
                          hx-headers='{"X-CSRFToken":"{{ csrf_token }}"}'>
                    <i class="fa-regular fa-star"></i> Mark authoritative
                  </button>
                {% endif %}
              {% endif %}
            </div>
          </div>
          <div class="flex flex-col gap-1">
            {% for field_name, field_value in ann.fields %}
              <div class="flex gap-2 text-sm">
                <span class="font-medium text-gray-500 min-w-24">{{ field_name }}:</span>
                <span>{{ field_value|default:'—' }}</span>
              </div>
            {% endfor %}
          </div>
        </div>
      {% endfor %}
    </div>
  </div>
</div>
```

Wrap this partial's outermost `<div>` with `id="annotation-list"` so HTMX `outerHTML` swaps target it. Replace the opening `<div class="card bg-base-100 shadow-sm">` with `<div id="annotation-list" class="card bg-base-100 shadow-sm">`.

- [ ] **Step 6: Run the view tests**

```bash
uv run pytest apps/human_annotations/tests/test_authoritative.py -v
```

Expected: PASS (all 14 tests in the file).

- [ ] **Step 7: Run the full annotation test suite for regressions**

```bash
uv run pytest apps/human_annotations/tests/ -v
```

Expected: PASS. Existing tests that build annotation context might now fail because the `annotations` dicts gained new keys — that should not cause failures unless tests assert exact dict shape. Investigate if so.

- [ ] **Step 8: Lint and format**

```bash
uv run ruff check apps/human_annotations/views/annotate_views.py apps/human_annotations/urls.py apps/human_annotations/tests/test_authoritative.py --fix
uv run ruff format apps/human_annotations/views/annotate_views.py apps/human_annotations/urls.py apps/human_annotations/tests/test_authoritative.py
```

- [ ] **Step 9: Commit**

```bash
git add apps/human_annotations/views/annotate_views.py apps/human_annotations/urls.py apps/human_annotations/tests/test_authoritative.py templates/human_annotations/partials/annotation_list.html
git commit -m "feat(human_annotations): SetAuthoritative view + URL + partial"
```

---

### Task 5: Aggregation prefers authoritative annotations

**Files:**
- Modify: `apps/human_annotations/aggregation.py`
- Modify: `apps/human_annotations/tests/test_aggregation.py`

- [ ] **Step 1: Add the failing aggregation tests**

Append to `apps/human_annotations/tests/test_aggregation.py`:

```python
from apps.human_annotations.models import (
    AnnotationItemStatus,
    AnnotationStatus,
)


@pytest.mark.django_db()
def test_aggregation_uses_only_authoritative_when_set(team, queue_with_int_schema):
    user1 = team.members.first()
    user2 = team.members.last()
    queue = queue_with_int_schema
    queue.num_reviews_required = 2
    queue.save(update_fields=["num_reviews_required"])

    session = ExperimentSessionFactory.create(team=team, chat__team=team)
    item = AnnotationItem.objects.create(
        queue=queue, team=team, item_type=AnnotationItemType.SESSION, session=session
    )
    # Two divergent annotations; admin picks the second as authoritative.
    Annotation.objects.create(
        item=item, team=team, reviewer=user1, data={"score": 1}, status=AnnotationStatus.SUBMITTED
    )
    auth = Annotation.objects.create(
        item=item, team=team, reviewer=user2, data={"score": 5}, status=AnnotationStatus.SUBMITTED
    )
    auth.is_authoritative = True
    auth.save(update_fields=["is_authoritative"])

    agg = compute_aggregates_for_queue(queue)

    # Only the authoritative value (5) should contribute.
    assert agg.aggregates["score"]["count"] == 1
    assert agg.aggregates["score"]["mean"] == 5.0


@pytest.mark.django_db()
def test_aggregation_falls_back_to_all_when_no_authoritative(team, queue_with_int_schema):
    user1 = team.members.first()
    user2 = team.members.last()
    queue = queue_with_int_schema
    queue.num_reviews_required = 2
    queue.save(update_fields=["num_reviews_required"])

    session = ExperimentSessionFactory.create(team=team, chat__team=team)
    item = AnnotationItem.objects.create(
        queue=queue, team=team, item_type=AnnotationItemType.SESSION, session=session
    )
    Annotation.objects.create(
        item=item, team=team, reviewer=user1, data={"score": 1}, status=AnnotationStatus.SUBMITTED
    )
    Annotation.objects.create(
        item=item, team=team, reviewer=user2, data={"score": 5}, status=AnnotationStatus.SUBMITTED
    )

    agg = compute_aggregates_for_queue(queue)

    # Both values should contribute when no authoritative pick.
    assert agg.aggregates["score"]["count"] == 2
    assert agg.aggregates["score"]["mean"] == 3.0
```

- [ ] **Step 2: Run the failing tests**

```bash
uv run pytest apps/human_annotations/tests/test_aggregation.py -v
```

Expected: `test_aggregation_uses_only_authoritative_when_set` FAILS — current aggregator counts both values; mean would be 3.0, count 2.

- [ ] **Step 3: Update the aggregator**

Replace the loop in `apps/human_annotations/aggregation.py` (currently around line 30-34) with per-item authoritative selection. Full updated `compute_aggregates_for_queue`:

```python
def compute_aggregates_for_queue(queue) -> AnnotationQueueAggregate:
    """Compute and store aggregates for all submitted annotations in a queue.

    Per item: use authoritative annotation if one exists, else fall back to all
    submitted annotations. Numeric / categorical aggregators are applied per field.
    Text (string) fields are excluded from aggregation.
    """
    aggregatable_fields = _get_aggregatable_fields(queue)
    field_values = defaultdict(list)
    items = queue.items.prefetch_related(
        Prefetch(
            "annotations",
            queryset=Annotation.objects.filter(status=AnnotationStatus.SUBMITTED),
        )
    ).all()

    for item in items:
        submitted = list(item.annotations.all())
        authoritative = [a for a in submitted if a.is_authoritative]
        contributing = authoritative if authoritative else submitted
        for ann in contributing:
            for field_name, value in ann.data.items():
                if field_name in aggregatable_fields and value is not None:
                    field_values[field_name].append(value)

    agg_data = {field_name: aggregate_field(values) for field_name, values in field_values.items()}

    obj, _ = AnnotationQueueAggregate.objects.update_or_create(
        queue=queue,
        defaults={"aggregates": agg_data, "team": queue.team},
    )
    return obj
```

- [ ] **Step 4: Run the tests**

```bash
uv run pytest apps/human_annotations/tests/test_aggregation.py -v
```

Expected: PASS (all, including the two new ones).

- [ ] **Step 5: Lint and format**

```bash
uv run ruff check apps/human_annotations/aggregation.py apps/human_annotations/tests/test_aggregation.py --fix
uv run ruff format apps/human_annotations/aggregation.py apps/human_annotations/tests/test_aggregation.py
```

- [ ] **Step 6: Commit**

```bash
git add apps/human_annotations/aggregation.py apps/human_annotations/tests/test_aggregation.py
git commit -m "feat(human_annotations): aggregator prefers authoritative annotations per item"
```

---

### Task 6: Surface awaiting/resolved progress numbers

**Files:**
- Modify: `apps/human_annotations/models.py`
- Modify: `apps/human_annotations/tests/test_models.py`

- [ ] **Step 1: Add failing progress test**

Append to `apps/human_annotations/tests/test_models.py`:

```python
@pytest.mark.django_db()
def test_get_progress_includes_awaiting_resolution(team):
    from apps.human_annotations.models import AnnotationItemStatus, AnnotationStatus

    user1 = team.members.first()
    from django.contrib.auth import get_user_model

    User = get_user_model()
    user2 = User.objects.create(username="r2", email="r2@e.com")
    team.members.add(user2)
    queue = AnnotationQueue.objects.create(
        team=team, name="Q", schema={}, created_by=user1, num_reviews_required=2
    )
    session = ExperimentSessionFactory.create(team=team, chat__team=team)
    item = AnnotationItem.objects.create(
        queue=queue, team=team, item_type=AnnotationItemType.SESSION, session=session
    )
    Annotation.objects.create(item=item, team=team, reviewer=user1, data={}, status=AnnotationStatus.SUBMITTED)
    Annotation.objects.create(item=item, team=team, reviewer=user2, data={}, status=AnnotationStatus.SUBMITTED)
    item.refresh_from_db()
    assert item.status == AnnotationItemStatus.AWAITING_RESOLUTION

    progress = queue.get_progress()

    assert progress["awaiting_resolution_items"] == 1
    assert progress["completed_items"] == 0
```

- [ ] **Step 2: Run the failing test**

```bash
uv run pytest apps/human_annotations/tests/test_models.py::test_get_progress_includes_awaiting_resolution -v
```

Expected: FAIL — `KeyError: 'awaiting_resolution_items'`.

- [ ] **Step 3: Add the key to `get_progress`**

Edit `AnnotationQueue.get_progress` in `apps/human_annotations/models.py` (currently around line 97). The full updated method:

```python
def get_progress(self):
    """Return progress stats including review-level progress for multi-review queues."""
    total_items = self.items.count()
    completed_items = self.items.filter(status=AnnotationItemStatus.COMPLETED).count()
    awaiting_resolution_items = self.items.filter(status=AnnotationItemStatus.AWAITING_RESOLUTION).count()
    flagged_items = self.items.filter(status=AnnotationItemStatus.FLAGGED).count()

    total_reviews_needed = total_items * self.num_reviews_required
    reviews_done = self.items.aggregate(total=Sum("review_count"))["total"] or 0
    review_percent = round((reviews_done / total_reviews_needed) * 100) if total_reviews_needed > 0 else 0

    return {
        "total_items": total_items,
        "completed_items": completed_items,
        "awaiting_resolution_items": awaiting_resolution_items,
        "flagged_items": flagged_items,
        "total_reviews_needed": total_reviews_needed,
        "reviews_done": reviews_done,
        "percent": review_percent,
    }
```

- [ ] **Step 4: Run the test**

```bash
uv run pytest apps/human_annotations/tests/test_models.py::test_get_progress_includes_awaiting_resolution -v
```

Expected: PASS.

- [ ] **Step 5: Lint and format**

```bash
uv run ruff check apps/human_annotations/models.py apps/human_annotations/tests/test_models.py --fix
uv run ruff format apps/human_annotations/models.py apps/human_annotations/tests/test_models.py
```

- [ ] **Step 6: Commit**

```bash
git add apps/human_annotations/models.py apps/human_annotations/tests/test_models.py
git commit -m "feat(human_annotations): add awaiting_resolution_items to queue progress"
```

---

### Task 7: Annotate page — authoritative banner & status pill styling

**Files:**
- Modify: `templates/human_annotations/annotate.html`
- Modify: `templates/human_annotations/columns/item_status.html`
- Modify: `apps/human_annotations/tests/test_authoritative.py`

- [ ] **Step 1: Add failing template-context test**

Append to `apps/human_annotations/tests/test_authoritative.py`:

```python
@pytest.mark.django_db()
def test_annotate_item_page_shows_awaiting_banner(admin_client, team, second_user):
    queue = _make_queue(team, num_reviews_required=2)
    item = _make_item(queue)
    user1 = team.members.last()  # the non-admin team member
    # Restrict assignees to reviewers so the admin sees the annotations-list view, not the form.
    queue.assignees.set([user1, second_user])
    Annotation.objects.create(
        item=item, team=team, reviewer=user1, data={}, status=AnnotationStatus.SUBMITTED
    )
    Annotation.objects.create(
        item=item, team=team, reviewer=second_user, data={}, status=AnnotationStatus.SUBMITTED
    )
    item.refresh_from_db()
    assert item.status == AnnotationItemStatus.AWAITING_RESOLUTION

    url = reverse(
        "human_annotations:annotate_item",
        args=[team.slug, queue.pk, item.pk],
    )
    response = admin_client.get(url)

    assert response.status_code == 200
    assert b"awaiting resolution" in response.content.lower()
    # Admin should see at least one Mark authoritative button.
    assert b"Mark authoritative" in response.content
```

- [ ] **Step 2: Run the failing test**

```bash
uv run pytest apps/human_annotations/tests/test_authoritative.py::test_annotate_item_page_shows_awaiting_banner -v
```

Expected: FAIL — banner text missing from template.

- [ ] **Step 3: Add the AWAITING_RESOLUTION pill styling**

Edit `templates/human_annotations/columns/item_status.html`. Insert before the `{% elif record.status == "completed" %}` branch:

```django
{% elif record.status == "awaiting_resolution" %}
  <span class="badge badge-soft badge-warning">{{ record.get_status_display }}</span>
```

- [ ] **Step 4: Replace the annotations block in `annotate.html` with the partial; add the banner**

Edit `templates/human_annotations/annotate.html`. Replace the `{% elif annotations %} ... {% endif %}` block (currently lines 137-170) with:

```django
{% elif annotations %}
  {% if item.status == "awaiting_resolution" %}
    <div role="alert" class="alert alert-warning alert-soft">
      <i class="fa-solid fa-circle-exclamation"></i>
      <span>All required reviews submitted. An admin must mark one annotation as authoritative to resolve.</span>
    </div>
  {% endif %}
  {% include "human_annotations/partials/annotation_list.html" %}
{% endif %}
```

- [ ] **Step 5: Run the test**

```bash
uv run pytest apps/human_annotations/tests/test_authoritative.py::test_annotate_item_page_shows_awaiting_banner -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add templates/human_annotations/annotate.html templates/human_annotations/columns/item_status.html apps/human_annotations/tests/test_authoritative.py
git commit -m "feat(human_annotations): awaiting-resolution banner + status pill"
```

---

### Task 8: Queue detail — surface resolved/awaiting figures

**Files:**
- Modify: `templates/human_annotations/queue_detail.html`
- Modify: `apps/human_annotations/tests/test_views.py`

- [ ] **Step 1: Add failing queue-detail test**

Append to `apps/human_annotations/tests/test_views.py`:

```python
@pytest.mark.django_db()
def test_queue_detail_shows_awaiting_resolution_callout(client, team_with_users, user):
    from django.contrib.auth import get_user_model

    User_ = get_user_model()
    user2 = User_.objects.create(username="r2-detail", email="r2-detail@e.com")
    team_with_users.members.add(user2)
    queue = AnnotationQueueFactory.create(team=team_with_users, created_by=user, num_reviews_required=2)
    item = AnnotationItemFactory.create(queue=queue, team=team_with_users)
    Annotation.objects.create(
        item=item, team=team_with_users, reviewer=user, data={}, status=AnnotationStatus.SUBMITTED
    )
    Annotation.objects.create(
        item=item, team=team_with_users, reviewer=user2, data={}, status=AnnotationStatus.SUBMITTED
    )
    item.refresh_from_db()
    assert item.status == AnnotationItemStatus.AWAITING_RESOLUTION

    url = reverse("human_annotations:queue_detail", args=[team_with_users.slug, queue.pk])
    response = client.get(url)

    assert response.status_code == 200
    assert b"awaiting resolution" in response.content.lower()
    assert b"1" in response.content  # the count
```

- [ ] **Step 2: Run the failing test**

```bash
uv run pytest apps/human_annotations/tests/test_views.py::test_queue_detail_shows_awaiting_resolution_callout -v
```

Expected: FAIL — no awaiting-resolution callout in template.

- [ ] **Step 3: Update the queue detail template**

Edit `templates/human_annotations/queue_detail.html`. In the progress card (currently lines 57-69), replace the inner `<div class="flex flex-wrap gap-x-4 gap-y-1 text-sm text-gray-500 mt-1">` block with:

```django
<div class="flex flex-wrap gap-x-4 gap-y-1 text-sm text-gray-500 mt-1">
  <span>{{ progress.reviews_done }}/{{ progress.total_reviews_needed }} reviews ({{ progress.percent }}%)</span>
  <span>{{ progress.completed_items }}/{{ progress.total_items }} items resolved</span>
  {% if progress.awaiting_resolution_items %}
    <span class="text-warning">{{ progress.awaiting_resolution_items }} awaiting resolution</span>
  {% endif %}
  {% if progress.flagged_items %}
    <span class="text-warning">{{ progress.flagged_items }} flagged</span>
  {% endif %}
</div>
```

- [ ] **Step 4: Run the test**

```bash
uv run pytest apps/human_annotations/tests/test_views.py::test_queue_detail_shows_awaiting_resolution_callout -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add templates/human_annotations/queue_detail.html apps/human_annotations/tests/test_views.py
git commit -m "feat(human_annotations): queue detail surfaces resolved & awaiting figures"
```

---

### Task 9: Items-table summary — star prefix for authoritative

**Files:**
- Modify: `templates/human_annotations/columns/annotations_summary.html`
- Modify: `apps/human_annotations/views/queue_views.py`
- Modify: `apps/human_annotations/tests/test_views.py`

- [ ] **Step 1: Add the failing test**

Append to `apps/human_annotations/tests/test_views.py`:

```python
@pytest.mark.django_db()
def test_items_table_marks_authoritative_with_star(client, team_with_users, user):
    queue = AnnotationQueueFactory.create(team=team_with_users, created_by=user, num_reviews_required=1)
    item = AnnotationItemFactory.create(queue=queue, team=team_with_users)
    ann = Annotation.objects.create(
        item=item, team=team_with_users, reviewer=user, data={"score": 5}, status=AnnotationStatus.SUBMITTED
    )
    ann.refresh_from_db()
    assert ann.is_authoritative is True

    url = reverse("human_annotations:queue_items_table", args=[team_with_users.slug, queue.pk])
    response = client.get(url)

    assert response.status_code == 200
    # The star glyph (fa-star) should appear in the rendered annotations summary.
    assert b"fa-star" in response.content
```

- [ ] **Step 2: Run the failing test**

```bash
uv run pytest apps/human_annotations/tests/test_views.py::test_items_table_marks_authoritative_with_star -v
```

Expected: FAIL — current template does not render a star.

- [ ] **Step 3: Update the summary template**

Replace `templates/human_annotations/columns/annotations_summary.html` with:

```django
{% with annotations=record.submitted_annotations %}
  {% if annotations %}
    {% for ann in annotations|slice:":3" %}
      <div class="text-xs">
        {% if ann.is_authoritative %}<i class="fa-solid fa-star text-primary" title="Authoritative"></i> {% endif %}<span class="font-medium">{{ ann.reviewer.get_full_name|default:ann.reviewer.username }}</span>:
        {% for key, value in ann.data.items %}{% if forloop.counter <= 3 %}{% if not forloop.first %}, {% endif %}{{ key }}: {{ value }}{% endif %}{% endfor %}
      </div>
    {% endfor %}
    {% if annotations|length > 3 %}
      <div class="text-xs text-gray-400">+{{ annotations|length|add:'-3' }} more</div>
    {% endif %}
  {% else %}
    <span class="text-gray-400 text-xs">No annotations</span>
  {% endif %}
{% endwith %}
```

- [ ] **Step 4: Confirm the prefetch keeps `is_authoritative` available**

The existing prefetch in `apps/human_annotations/views/queue_views.py` `AnnotationQueueItemsTableView.get_queryset` already loads the full `Annotation` rows, so `is_authoritative` is included. No view change needed — but the template now reads `ann.is_authoritative`, which the existing prefetch covers. No edit required to `queue_views.py` for this task.

- [ ] **Step 5: Run the test**

```bash
uv run pytest apps/human_annotations/tests/test_views.py::test_items_table_marks_authoritative_with_star -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add templates/human_annotations/columns/annotations_summary.html apps/human_annotations/tests/test_views.py
git commit -m "feat(human_annotations): star prefix on authoritative annotation in items summary"
```

---

### Task 10: Export `is_authoritative` column

**Files:**
- Modify: `apps/human_annotations/views/queue_views.py`
- Modify: `apps/human_annotations/tests/test_views.py`

- [ ] **Step 1: Add failing export tests**

Append to `apps/human_annotations/tests/test_views.py`:

```python
@pytest.mark.django_db()
def test_export_csv_includes_is_authoritative(client, team_with_users, user):
    queue = AnnotationQueueFactory.create(team=team_with_users, created_by=user, num_reviews_required=1)
    item = AnnotationItemFactory.create(queue=queue, team=team_with_users)
    Annotation.objects.create(
        item=item, team=team_with_users, reviewer=user, data={"score": 5}, status=AnnotationStatus.SUBMITTED
    )

    url = reverse("human_annotations:queue_export", args=[team_with_users.slug, queue.pk])
    response = client.get(url + "?format=csv")

    assert response.status_code == 200
    content = response.content.decode()
    reader = csv.DictReader(io.StringIO(content))
    fieldnames = reader.fieldnames
    assert "is_authoritative" in fieldnames
    rows = list(reader)
    assert rows[0]["is_authoritative"] == "True"


@pytest.mark.django_db()
def test_export_jsonl_includes_is_authoritative(client, team_with_users, user):
    queue = AnnotationQueueFactory.create(team=team_with_users, created_by=user, num_reviews_required=1)
    item = AnnotationItemFactory.create(queue=queue, team=team_with_users)
    Annotation.objects.create(
        item=item, team=team_with_users, reviewer=user, data={"score": 5}, status=AnnotationStatus.SUBMITTED
    )

    url = reverse("human_annotations:queue_export", args=[team_with_users.slug, queue.pk])
    response = client.get(url + "?format=jsonl")

    assert response.status_code == 200
    lines = response.content.decode().strip().splitlines()
    record = json.loads(lines[0])
    assert "is_authoritative" in record
    assert record["is_authoritative"] is True
```

- [ ] **Step 2: Run the failing tests**

```bash
uv run pytest apps/human_annotations/tests/test_views.py::test_export_csv_includes_is_authoritative apps/human_annotations/tests/test_views.py::test_export_jsonl_includes_is_authoritative -v
```

Expected: FAIL — `is_authoritative` not in CSV header / JSONL record.

- [ ] **Step 3: Update `ExportAnnotations`**

Edit `apps/human_annotations/views/queue_views.py`. In `_export_csv` (around line 542), update the `fieldnames` list:

```python
fieldnames = [
    "item_id",
    "item_type",
    "session_id",
    "annotated_at",
    "flagged",
    "is_authoritative",
    "flags",
] + schema_fields
```

In the per-annotation row construction inside `_export_csv`, add the `is_authoritative` key. Replace the dict literal:

```python
row = {
    "item_id": ann.item_id,
    "item_type": ann.item.item_type,
    "session_id": self._get_session_external_id(ann.item),
    "annotated_at": ann.created_at.isoformat(),
    "flagged": False,
    "is_authoritative": ann.is_authoritative,
    "flags": json.dumps(ann.item.flags),
}
```

In `_build_flagged_row` (around line 532), add the key:

```python
def _build_flagged_row(self, item):
    return {
        "item_id": item.pk,
        "item_type": item.item_type,
        "session_id": self._get_session_external_id(item),
        "annotated_at": "",
        "flagged": True,
        "is_authoritative": False,
        "flags": item.flags,
    }
```

In `_export_jsonl` (around line 581), update the per-annotation record:

```python
record = {
    "item_id": ann.item_id,
    "item_type": ann.item.item_type,
    "session_id": self._get_session_external_id(ann.item),
    "annotated_at": ann.created_at.isoformat(),
    "flagged": False,
    "is_authoritative": ann.is_authoritative,
    "flags": ann.item.flags,
    "annotation": ann.data,
}
```

- [ ] **Step 4: Run the tests**

```bash
uv run pytest apps/human_annotations/tests/test_views.py::test_export_csv_includes_is_authoritative apps/human_annotations/tests/test_views.py::test_export_jsonl_includes_is_authoritative -v
```

Expected: PASS.

- [ ] **Step 5: Run the full view-test suite**

```bash
uv run pytest apps/human_annotations/tests/test_views.py -v
```

Expected: PASS. Any existing export test that assumes the old field set needs updating — adjust assertions to include `is_authoritative` (do not remove fields).

- [ ] **Step 6: Lint and format**

```bash
uv run ruff check apps/human_annotations/views/queue_views.py apps/human_annotations/tests/test_views.py --fix
uv run ruff format apps/human_annotations/views/queue_views.py apps/human_annotations/tests/test_views.py
```

- [ ] **Step 7: Commit**

```bash
git add apps/human_annotations/views/queue_views.py apps/human_annotations/tests/test_views.py
git commit -m "feat(human_annotations): include is_authoritative in CSV & JSONL export"
```

---

### Task 11: Data backfill migration

**Files:**
- Create: `apps/human_annotations/migrations/0004_backfill_authoritative.py`
- Modify: `apps/human_annotations/tests/test_authoritative.py`

- [ ] **Step 1: Add a failing backfill test**

Append to `apps/human_annotations/tests/test_authoritative.py`:

```python
@pytest.mark.django_db()
def test_backfill_function_marks_single_reviewer_and_downgrades_completed(team, second_user):
    """Direct test of the backfill helper. Since Django migrations have already
    run in the test DB, we exercise the helper function in isolation by setting
    up state that mimics pre-migration data and calling the forwards function."""
    import importlib

    from django.apps import apps as django_apps

    migration_module = importlib.import_module(
        "apps.human_annotations.migrations.0004_backfill_authoritative"
    )
    forwards = migration_module.forwards

    # Setup: single-reviewer queue with one submitted annotation that is NOT yet authoritative.
    single_q = _make_queue(team, num_reviews_required=1)
    single_item = _make_item(single_q)
    user = team.members.first()
    single_ann = Annotation.objects.create(
        item=single_item, team=team, reviewer=user, data={"score": 5}, status=AnnotationStatus.SUBMITTED
    )
    # Strip the auto-mark to simulate pre-migration data.
    Annotation.objects.filter(pk=single_ann.pk).update(
        is_authoritative=False, authoritative_set_by=None, authoritative_set_at=None
    )

    # Setup: multi-reviewer queue with item already at COMPLETED but no authoritative annotation.
    multi_q = _make_queue(team, num_reviews_required=2)
    multi_item = _make_item(multi_q)
    Annotation.objects.create(
        item=multi_item, team=team, reviewer=user, data={}, status=AnnotationStatus.SUBMITTED
    )
    Annotation.objects.create(
        item=multi_item, team=team, reviewer=second_user, data={}, status=AnnotationStatus.SUBMITTED
    )
    # Force pre-migration state.
    AnnotationItem.objects.filter(pk=multi_item.pk).update(status=AnnotationItemStatus.COMPLETED)

    # Run the backfill in isolation.
    forwards(django_apps, None)

    single_ann.refresh_from_db()
    multi_item.refresh_from_db()

    assert single_ann.is_authoritative is True
    assert single_ann.authoritative_set_at is not None
    assert multi_item.status == AnnotationItemStatus.AWAITING_RESOLUTION
```

- [ ] **Step 2: Run the failing test**

```bash
uv run pytest apps/human_annotations/tests/test_authoritative.py::test_backfill_function_marks_single_reviewer_and_downgrades_completed -v
```

Expected: FAIL — migration module does not exist.

- [ ] **Step 3: Create the backfill migration**

Create `apps/human_annotations/migrations/0004_backfill_authoritative.py`:

```python
from django.db import migrations
from django.utils import timezone


def forwards(apps, schema_editor):
    """Backfill the authoritative flag.

    Two operations:
    1. For each item in a queue with num_reviews_required==1 that has exactly one
       submitted annotation, mark that annotation as authoritative.
    2. For each item currently at COMPLETED in a multi-reviewer queue with no
       authoritative annotation, downgrade to AWAITING_RESOLUTION.
    """
    Annotation = apps.get_model("human_annotations", "Annotation")
    AnnotationItem = apps.get_model("human_annotations", "AnnotationItem")
    now = timezone.now()

    # (1) Single-reviewer auto-mark.
    for item in AnnotationItem.objects.filter(queue__num_reviews_required=1):
        submitted = list(item.annotations.filter(status="submitted"))
        if len(submitted) == 1 and not submitted[0].is_authoritative:
            ann = submitted[0]
            ann.is_authoritative = True
            ann.authoritative_set_by = None
            ann.authoritative_set_at = now
            ann.save(update_fields=["is_authoritative", "authoritative_set_by", "authoritative_set_at"])

    # (2) Multi-reviewer COMPLETED items without authoritative → AWAITING_RESOLUTION.
    for item in AnnotationItem.objects.filter(
        queue__num_reviews_required__gt=1, status="completed"
    ):
        has_auth = item.annotations.filter(is_authoritative=True).exists()
        if not has_auth:
            item.status = "awaiting_resolution"
            item.save(update_fields=["status"])


def backwards(apps, schema_editor):
    """Best-effort reverse: clear authoritative flags set by this backfill (those
    with set_by=None) and revert AWAITING_RESOLUTION to COMPLETED."""
    Annotation = apps.get_model("human_annotations", "Annotation")
    AnnotationItem = apps.get_model("human_annotations", "AnnotationItem")
    Annotation.objects.filter(is_authoritative=True, authoritative_set_by__isnull=True).update(
        is_authoritative=False, authoritative_set_at=None
    )
    AnnotationItem.objects.filter(status="awaiting_resolution").update(status="completed")


class Migration(migrations.Migration):
    dependencies = [
        ("human_annotations", "0003_authoritative_annotation_fields"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
```

- [ ] **Step 4: Run the test**

```bash
uv run pytest apps/human_annotations/tests/test_authoritative.py::test_backfill_function_marks_single_reviewer_and_downgrades_completed -v
```

Expected: PASS.

- [ ] **Step 5: Run the full annotation test suite**

```bash
uv run pytest apps/human_annotations/tests/ -v
```

Expected: PASS.

- [ ] **Step 6: Lint and format**

```bash
uv run ruff check apps/human_annotations/migrations/0004_backfill_authoritative.py apps/human_annotations/tests/test_authoritative.py --fix
uv run ruff format apps/human_annotations/migrations/0004_backfill_authoritative.py apps/human_annotations/tests/test_authoritative.py
```

- [ ] **Step 7: Commit**

```bash
git add apps/human_annotations/migrations/0004_backfill_authoritative.py apps/human_annotations/tests/test_authoritative.py
git commit -m "feat(human_annotations): backfill authoritative flag & status for existing data"
```

---

### Task 12: Final verification

**Files:** None (verification only).

- [ ] **Step 1: Full app test suite**

```bash
uv run pytest apps/human_annotations/ apps/evaluations/tests/test_import_from_annotation_queue.py -v
```

Expected: ALL PASS.

- [ ] **Step 2: Ruff over all touched files**

```bash
uv run ruff check apps/human_annotations/ --fix
uv run ruff format apps/human_annotations/
```

Expected: no remaining issues.

- [ ] **Step 3: Type-check the touched module**

```bash
uv run ty check apps/human_annotations/
```

Expected: PASS. If failures touch unrelated existing code, report; do not fix.

- [ ] **Step 4: Sanity-check the migrations apply cleanly on a fresh DB**

```bash
uv run python manage.py migrate human_annotations zero
uv run python manage.py migrate human_annotations
```

Expected: both commands complete without errors.

- [ ] **Step 5: Manual UI smoke-test (browser)**

Start the dev server:

```bash
uv run inv runserver
```

In a browser, as a team owner / queue admin:
1. Create a queue with `num_reviews_required=2`, add two assignees, add a session.
2. As reviewer A, submit an annotation. Verify item shows `IN_PROGRESS`.
3. As reviewer B, submit a divergent annotation. Verify item shows `AWAITING_RESOLUTION` and the annotate page shows the awaiting-resolution banner with Mark authoritative buttons.
4. As admin, click Mark authoritative on one annotation. Verify the row gains the gold Authoritative badge, item status flips to COMPLETED, queue progress shows resolved count.
5. Click Mark authoritative on the *other* annotation. Verify the badge moves; old row reverts to no-badge.
6. Click Clear authoritative. Verify item reverts to AWAITING_RESOLUTION.
7. Export CSV. Verify the `is_authoritative` column is present.

Expected: all observations match. If any step fails, report which one and stop.

---

## Self-review notes

- Spec sections mapped to tasks:
  - Data model (3 fields, partial unique, AWAITING_RESOLUTION enum) → Task 1
  - Auto-mark single-reviewer → Task 2
  - Status transitions → Task 3
  - Admin toggle endpoint → Task 4
  - Aggregation → Task 5
  - Progress / get_progress → Task 6
  - Annotate.html UI (badge, banner) + status pill → Task 7
  - Queue detail UI → Task 8
  - Items summary star → Task 9
  - Export columns → Task 10
  - Data backfill → Task 11
  - Final verification → Task 12
- Status filter dropdown: handled implicitly by `AnnotationItemStatusFilter` reading `AnnotationItemStatus.choices` (`apps/human_annotations/filters.py:28`). Added a sanity check via the items-table test in Task 9 (response renders without error).
- Edit-an-authoritative-annotation invariant: verified by the existing `EditAnnotation.post` path, which already does `annotation.save(update_fields=["data", "updated_at"])` and `recompute_queue_aggregates`. Our new fields are not in those `update_fields`, so they're preserved across edits. Manual smoke-test step 4 exercises this implicitly.
- All test code in the plan is complete; no placeholders or "similar to above". Each task contains the full file content where new files are created.
