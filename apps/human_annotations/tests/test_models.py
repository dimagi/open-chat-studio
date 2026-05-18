import django.db
import pytest
from django.contrib.auth import get_user_model

from apps.human_annotations.models import (
    Annotation,
    AnnotationItem,
    AnnotationItemType,
    AnnotationQueue,
)
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.team import MembershipFactory, TeamWithUsersFactory


@pytest.fixture()
def team():
    return TeamWithUsersFactory.create()


@pytest.mark.django_db()
def test_queue_get_field_definitions(team):
    user = team.members.first()
    queue = AnnotationQueue.objects.create(
        team=team,
        name="Test",
        schema={
            "score": {"type": "int", "description": "Score", "ge": 1, "le": 5},
        },
        created_by=user,
    )
    field_defs = queue.get_field_definitions()
    assert "score" in field_defs
    assert field_defs["score"].type == "int"
    assert field_defs["score"].ge == 1
    assert field_defs["score"].le == 5


@pytest.mark.django_db()
def test_queue_progress_empty(team):
    user = team.members.first()
    queue = AnnotationQueue.objects.create(
        team=team,
        name="Empty Queue",
        schema={},
        created_by=user,
    )
    progress = queue.get_progress()
    assert progress["total_items"] == 0
    assert progress["completed_items"] == 0
    assert progress["percent"] == 0


@pytest.mark.django_db()
def test_item_prevents_duplicate_session_in_queue(team):
    user = team.members.first()
    queue = AnnotationQueue.objects.create(team=team, name="Q", schema={}, created_by=user)
    session = ExperimentSessionFactory.create(team=team, chat__team=team)

    AnnotationItem.objects.create(
        queue=queue,
        team=team,
        item_type=AnnotationItemType.SESSION,
        session=session,
    )
    with pytest.raises(django.db.IntegrityError):
        AnnotationItem.objects.create(
            queue=queue,
            team=team,
            item_type=AnnotationItemType.SESSION,
            session=session,
        )


@pytest.mark.django_db()
def test_annotation_prevents_duplicate_reviewer(team):
    user = team.members.first()
    queue = AnnotationQueue.objects.create(team=team, name="Q", schema={}, created_by=user)
    session = ExperimentSessionFactory.create(team=team, chat__team=team)
    item = AnnotationItem.objects.create(
        queue=queue,
        team=team,
        item_type=AnnotationItemType.SESSION,
        session=session,
    )

    Annotation.objects.create(item=item, team=team, reviewer=user, data={})
    with pytest.raises(django.db.IntegrityError):
        Annotation.objects.create(item=item, team=team, reviewer=user, data={})


@pytest.mark.django_db()
def test_only_one_authoritative_annotation_per_item(team):
    User = get_user_model()
    user1 = team.members.first()
    user2 = User.objects.create(username="reviewer2", email="r2@example.com")
    MembershipFactory.create(team=team, user=user2)
    queue = AnnotationQueue.objects.create(team=team, name="Q", schema={}, created_by=user1, num_reviews_required=2)
    session = ExperimentSessionFactory.create(team=team, chat__team=team)
    item = AnnotationItem.objects.create(queue=queue, team=team, item_type=AnnotationItemType.SESSION, session=session)
    Annotation.objects.create(item=item, team=team, reviewer=user1, data={}, is_authoritative=True)
    with pytest.raises(django.db.IntegrityError):
        Annotation.objects.create(item=item, team=team, reviewer=user2, data={}, is_authoritative=True)


@pytest.mark.django_db()
def test_authoritative_constraint_allows_one_per_item_across_different_items(team):
    user = team.members.first()
    queue = AnnotationQueue.objects.create(team=team, name="Q", schema={}, created_by=user, num_reviews_required=1)
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
