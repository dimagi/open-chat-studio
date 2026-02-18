import pytest

from apps.human_annotations.aggregation import compute_aggregates_for_queue
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


@pytest.fixture()
def queue_with_int_schema(team):
    return AnnotationQueue.objects.create(
        team=team,
        name="Numeric Queue",
        schema={"score": {"type": "int", "description": "Score 1-5", "ge": 1, "le": 5}},
        created_by=team.members.first(),
    )


@pytest.fixture()
def queue_with_choice_schema(team):
    return AnnotationQueue.objects.create(
        team=team,
        name="Choice Queue",
        schema={
            "category": {
                "type": "choice",
                "description": "Category",
                "choices": ["good", "bad", "neutral"],
            },
        },
        created_by=team.members.first(),
    )


def _make_item_and_annotate(queue, team, reviewer, data):
    session = ExperimentSessionFactory(team=team, chat__team=team)
    item = AnnotationItem.objects.create(queue=queue, team=team, item_type=AnnotationItemType.SESSION, session=session)
    Annotation.objects.create(item=item, team=team, reviewer=reviewer, data=data)
    return item


@pytest.mark.django_db()
def test_compute_aggregates_numeric(team, queue_with_int_schema):
    user1 = team.members.first()
    user2 = team.members.last()

    # Create 3 items each annotated once
    _make_item_and_annotate(queue_with_int_schema, team, user1, {"score": 3})
    _make_item_and_annotate(queue_with_int_schema, team, user1, {"score": 4})
    _make_item_and_annotate(queue_with_int_schema, team, user2, {"score": 5})

    agg = compute_aggregates_for_queue(queue_with_int_schema)
    assert "score" in agg.aggregates
    stats = agg.aggregates["score"]
    assert stats["type"] == "numeric"
    assert stats["count"] == 3
    assert stats["mean"] == 4.0
    assert stats["min"] == 3
    assert stats["max"] == 5


@pytest.mark.django_db()
def test_compute_aggregates_categorical(team, queue_with_choice_schema):
    user1 = team.members.first()
    user2 = team.members.last()

    _make_item_and_annotate(queue_with_choice_schema, team, user1, {"category": "good"})
    _make_item_and_annotate(queue_with_choice_schema, team, user1, {"category": "good"})
    _make_item_and_annotate(queue_with_choice_schema, team, user2, {"category": "bad"})

    agg = compute_aggregates_for_queue(queue_with_choice_schema)
    stats = agg.aggregates["category"]
    assert stats["type"] == "categorical"
    assert stats["count"] == 3
    assert stats["mode"] == "good"
    assert "distribution" in stats
    assert stats["distribution"]["good"] == pytest.approx(66.7, abs=0.1)
    assert stats["distribution"]["bad"] == pytest.approx(33.3, abs=0.1)


@pytest.mark.django_db()
def test_compute_aggregates_empty_queue(team):
    queue = AnnotationQueue.objects.create(
        team=team,
        name="Empty",
        schema={"score": {"type": "int", "description": "Score", "ge": 1, "le": 5}},
        created_by=team.members.first(),
    )
    agg = compute_aggregates_for_queue(queue)
    assert agg.aggregates == {}


@pytest.mark.django_db()
def test_compute_aggregates_updates_on_recompute(team, queue_with_int_schema):
    user1 = team.members.first()
    user2 = team.members.last()

    _make_item_and_annotate(queue_with_int_schema, team, user1, {"score": 3})
    agg1 = compute_aggregates_for_queue(queue_with_int_schema)
    assert agg1.aggregates["score"]["count"] == 1
    assert agg1.aggregates["score"]["mean"] == 3.0

    _make_item_and_annotate(queue_with_int_schema, team, user2, {"score": 5})
    agg2 = compute_aggregates_for_queue(queue_with_int_schema)
    assert agg2.pk == agg1.pk  # same object, updated
    assert agg2.aggregates["score"]["count"] == 2
    assert agg2.aggregates["score"]["mean"] == 4.0


@pytest.mark.django_db()
def test_compute_aggregates_excludes_string_fields(team):
    """String/text fields should be excluded from aggregation."""
    queue = AnnotationQueue.objects.create(
        team=team,
        name="Mixed Queue",
        schema={
            "score": {"type": "int", "description": "Score 1-5", "ge": 1, "le": 5},
            "notes": {"type": "string", "description": "Free text notes"},
            "category": {"type": "choice", "description": "Category", "choices": ["good", "bad"]},
        },
        created_by=team.members.first(),
    )
    user = team.members.first()
    _make_item_and_annotate(queue, team, user, {"score": 4, "notes": "looks good", "category": "good"})
    _make_item_and_annotate(queue, team, user, {"score": 5, "notes": "great", "category": "good"})

    agg = compute_aggregates_for_queue(queue)
    assert "score" in agg.aggregates
    assert "category" in agg.aggregates
    assert "notes" not in agg.aggregates


@pytest.mark.django_db()
def test_aggregates_auto_recompute_on_annotation_save(team, queue_with_int_schema):
    """Annotation.save() should trigger aggregate recomputation automatically."""
    user1 = team.members.first()
    user2 = team.members.last()

    session = ExperimentSessionFactory(team=team, chat__team=team)
    item = AnnotationItem.objects.create(
        queue=queue_with_int_schema, team=team, item_type=AnnotationItemType.SESSION, session=session
    )

    # First annotation triggers aggregate creation
    Annotation.objects.create(item=item, team=team, reviewer=user1, data={"score": 3})
    queue_with_int_schema.refresh_from_db()
    agg = queue_with_int_schema.aggregate
    assert agg.aggregates["score"]["count"] == 1
    assert agg.aggregates["score"]["mean"] == 3.0

    # Second annotation on a new item updates aggregate
    session2 = ExperimentSessionFactory(team=team, chat__team=team)
    item2 = AnnotationItem.objects.create(
        queue=queue_with_int_schema, team=team, item_type=AnnotationItemType.SESSION, session=session2
    )
    Annotation.objects.create(item=item2, team=team, reviewer=user2, data={"score": 5})
    agg.refresh_from_db()
    assert agg.aggregates["score"]["count"] == 2
    assert agg.aggregates["score"]["mean"] == 4.0
