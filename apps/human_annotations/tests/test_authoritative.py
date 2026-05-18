import pytest
from django.contrib.auth import get_user_model

from apps.human_annotations.models import (
    Annotation,
    AnnotationItem,
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
