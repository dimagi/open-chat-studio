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
