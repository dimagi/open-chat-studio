import django.db
import pytest

from apps.human_annotations.models import (
    Annotation,
    AnnotationItem,
    AnnotationItemType,
    AnnotationQueue,
)
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def team():
    return TeamWithUsersFactory()


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
    session = ExperimentSessionFactory(team=team, chat__team=team)

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
    session = ExperimentSessionFactory(team=team, chat__team=team)
    item = AnnotationItem.objects.create(
        queue=queue,
        team=team,
        item_type=AnnotationItemType.SESSION,
        session=session,
    )

    Annotation.objects.create(item=item, team=team, reviewer=user, data={})
    with pytest.raises(django.db.IntegrityError):
        Annotation.objects.create(item=item, team=team, reviewer=user, data={})
