import importlib

import pytest
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client
from django.urls import reverse

from apps.human_annotations.models import (
    Annotation,
    AnnotationItem,
    AnnotationItemStatus,
    AnnotationItemType,
    AnnotationQueue,
    AnnotationStatus,
)
from apps.teams.backends import ANNOTATION_REVIEWER_GROUP
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.team import MembershipFactory, TeamWithUsersFactory

User = get_user_model()


@pytest.fixture()
def team():
    return TeamWithUsersFactory.create()


@pytest.fixture()
def second_user(team):
    user = User.objects.create(username="reviewer2", email="r2@example.com")
    MembershipFactory.create(team=team, user=user)
    return user


@pytest.fixture()
def admin_client(team):
    """Team owner - has change_annotationqueue."""
    user = team.members.first()
    c = Client()
    c.force_login(user)
    return c


@pytest.fixture()
def reviewer_user(team):
    """A user with only the ANNOTATION_REVIEWER_GROUP (no change_annotationqueue)."""
    user = User.objects.create(username="reviewer-only", email="ro@example.com")
    membership = MembershipFactory.create(team=team, user=user)
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

    Annotation.objects.create(item=item, team=team, reviewer=user, data={"score": 5}, status=AnnotationStatus.SUBMITTED)
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


@pytest.mark.django_db()
def test_single_reviewer_completed_after_submission(team):
    queue = _make_queue(team, num_reviews_required=1)
    item = _make_item(queue)
    user = team.members.first()

    Annotation.objects.create(item=item, team=team, reviewer=user, data={"score": 5}, status=AnnotationStatus.SUBMITTED)

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
    item.refresh_from_db()
    item.update_status()

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
    ann1 = Annotation.objects.create(item=item, team=team, reviewer=user1, data={}, status=AnnotationStatus.SUBMITTED)
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
    ann1 = Annotation.objects.create(item=item, team=team, reviewer=user1, data={}, status=AnnotationStatus.SUBMITTED)
    Annotation.objects.create(item=item, team=team, reviewer=second_user, data={}, status=AnnotationStatus.SUBMITTED)
    admin_client.post(_set_authoritative_url(team, queue, item, ann1), {"value": "true"})

    admin_client.post(_set_authoritative_url(team, queue, item, ann1), {"value": "false"})

    ann1.refresh_from_db()
    item.refresh_from_db()
    assert ann1.is_authoritative is False
    assert ann1.authoritative_set_at is None
    assert item.status == AnnotationItemStatus.AWAITING_RESOLUTION


@pytest.mark.django_db()
def test_set_authoritative_allowed_for_reviewer(reviewer_client, team, second_user):
    queue = _make_queue(team, num_reviews_required=2)
    item = _make_item(queue)
    user1 = team.members.first()
    ann1 = Annotation.objects.create(item=item, team=team, reviewer=user1, data={}, status=AnnotationStatus.SUBMITTED)

    response = reviewer_client.post(_set_authoritative_url(team, queue, item, ann1), {"value": "true"})

    assert response.status_code == 200
    ann1.refresh_from_db()
    assert ann1.is_authoritative is True


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
    ann1 = Annotation.objects.create(item=item, team=team, reviewer=user1, data={}, status=AnnotationStatus.SUBMITTED)

    admin_client.post(_set_authoritative_url(team, queue, item, ann1), {"value": "true"})

    ann1.refresh_from_db()
    item.refresh_from_db()
    assert ann1.is_authoritative is True
    # Only one of two reviews submitted, so status should remain IN_PROGRESS.
    assert item.status == AnnotationItemStatus.IN_PROGRESS

    # When the second review arrives, item should now be COMPLETED (authoritative already set).
    Annotation.objects.create(item=item, team=team, reviewer=second_user, data={}, status=AnnotationStatus.SUBMITTED)
    item.refresh_from_db()
    assert item.status == AnnotationItemStatus.COMPLETED


@pytest.mark.django_db()
def test_set_authoritative_htmx_returns_partial(admin_client, team, second_user):
    queue = _make_queue(team, num_reviews_required=2)
    item = _make_item(queue)
    user1 = team.members.first()
    ann1 = Annotation.objects.create(item=item, team=team, reviewer=user1, data={}, status=AnnotationStatus.SUBMITTED)

    response = admin_client.post(
        _set_authoritative_url(team, queue, item, ann1),
        {"value": "true"},
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 200
    assert b"Authoritative" in response.content


@pytest.mark.django_db()
def test_annotate_item_page_shows_awaiting_banner(admin_client, team, second_user):
    queue = _make_queue(team, num_reviews_required=2)
    item = _make_item(queue)
    admin = team.members.first()
    user1 = team.members.exclude(pk__in=[admin.pk, second_user.pk]).first()  # the non-admin team member
    # Restrict assignees to reviewers so the admin sees the annotations-list view, not the form.
    queue.assignees.set([user1, second_user])
    Annotation.objects.create(item=item, team=team, reviewer=user1, data={}, status=AnnotationStatus.SUBMITTED)
    Annotation.objects.create(item=item, team=team, reviewer=second_user, data={}, status=AnnotationStatus.SUBMITTED)
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


@pytest.mark.django_db()
def test_backfill_function_marks_single_reviewer_and_downgrades_completed(team, second_user):
    """Direct test of the backfill helper. Since Django migrations have already
    run in the test DB, we exercise the helper function in isolation by setting
    up state that mimics pre-migration data and calling the forwards function."""
    migration_module = importlib.import_module("apps.human_annotations.migrations.0004_backfill_authoritative")
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
    Annotation.objects.create(item=multi_item, team=team, reviewer=user, data={}, status=AnnotationStatus.SUBMITTED)
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


@pytest.mark.django_db()
def test_backfill_picks_earliest_when_single_reviewer_item_has_multiple_submissions(team, second_user):
    """For single-reviewer queues with over-budget submissions, backfill marks the
    earliest submission authoritative."""
    migration_module = importlib.import_module("apps.human_annotations.migrations.0004_backfill_authoritative")
    forwards = migration_module.forwards

    queue = _make_queue(team, num_reviews_required=1)
    item = _make_item(queue)
    user1 = team.members.first()
    first = Annotation.objects.create(
        item=item, team=team, reviewer=user1, data={"score": 5}, status=AnnotationStatus.SUBMITTED
    )
    second = Annotation.objects.create(
        item=item, team=team, reviewer=second_user, data={"score": 3}, status=AnnotationStatus.SUBMITTED
    )
    Annotation.objects.filter(pk__in=[first.pk, second.pk]).update(is_authoritative=False, authoritative_set_at=None)

    forwards(django_apps, None)

    first.refresh_from_db()
    second.refresh_from_db()
    assert first.is_authoritative is True
    assert second.is_authoritative is False


@pytest.mark.django_db()
def test_htmx_swap_clears_awaiting_banner_when_authoritative_set(admin_client, team, second_user):
    """After marking authoritative via HTMX, the swapped partial should no longer
    contain the awaiting-resolution banner because the item is now COMPLETED."""
    queue = _make_queue(team, num_reviews_required=2)
    item = _make_item(queue)
    admin = team.members.first()
    user1 = team.members.exclude(pk__in=[admin.pk, second_user.pk]).first()
    queue.assignees.set([user1, second_user])
    ann1 = Annotation.objects.create(item=item, team=team, reviewer=user1, data={}, status=AnnotationStatus.SUBMITTED)
    Annotation.objects.create(item=item, team=team, reviewer=second_user, data={}, status=AnnotationStatus.SUBMITTED)
    item.refresh_from_db()
    assert item.status == AnnotationItemStatus.AWAITING_RESOLUTION

    response = admin_client.post(
        _set_authoritative_url(team, queue, item, ann1),
        {"value": "true"},
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 200
    # The swapped partial should NOT contain the banner — item is now COMPLETED.
    assert b"Awaiting resolution." not in response.content
    # And the swap target should be present.
    assert b'id="annotate-summary"' in response.content


@pytest.mark.django_db()
def test_htmx_swap_shows_awaiting_banner_when_authoritative_cleared(admin_client, team, second_user):
    """After clearing authoritative via HTMX, the swapped partial should contain
    the awaiting-resolution banner because the item is back to AWAITING_RESOLUTION."""
    queue = _make_queue(team, num_reviews_required=2)
    item = _make_item(queue)
    admin = team.members.first()
    user1 = team.members.exclude(pk__in=[admin.pk, second_user.pk]).first()
    queue.assignees.set([user1, second_user])
    ann1 = Annotation.objects.create(item=item, team=team, reviewer=user1, data={}, status=AnnotationStatus.SUBMITTED)
    Annotation.objects.create(item=item, team=team, reviewer=second_user, data={}, status=AnnotationStatus.SUBMITTED)
    admin_client.post(_set_authoritative_url(team, queue, item, ann1), {"value": "true"})

    response = admin_client.post(
        _set_authoritative_url(team, queue, item, ann1),
        {"value": "false"},
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 200
    assert b"Awaiting resolution." in response.content


@pytest.mark.django_db()
def test_set_authoritative_rejects_draft_annotation(admin_client, team):
    """Direct POSTs cannot mark a DRAFT annotation as authoritative; only SUBMITTED counts."""
    queue = _make_queue(team, num_reviews_required=2)
    item = _make_item(queue)
    user = team.members.first()
    draft = Annotation.objects.create(item=item, team=team, reviewer=user, data={}, status=AnnotationStatus.DRAFT)

    response = admin_client.post(_set_authoritative_url(team, queue, item, draft), {"value": "true"})

    assert response.status_code == 404
    draft.refresh_from_db()
    assert draft.is_authoritative is False
