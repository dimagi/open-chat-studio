import django.db
import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

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
    user3 = User.objects.create(username="reviewer3", email="r3@example.com")
    MembershipFactory.create(team=team, user=user2)
    MembershipFactory.create(team=team, user=user3)
    queue = AnnotationQueue.objects.create(team=team, name="Q", schema={}, created_by=user1, num_reviews_required=3)
    session = ExperimentSessionFactory.create(team=team, chat__team=team)
    item = AnnotationItem.objects.create(queue=queue, team=team, item_type=AnnotationItemType.SESSION, session=session)
    # Non-authoritative annotations from multiple reviewers coexist (partial constraint).
    Annotation.objects.create(item=item, team=team, reviewer=user3, data={}, is_authoritative=False)
    # First authoritative annotation succeeds.
    Annotation.objects.create(item=item, team=team, reviewer=user1, data={}, is_authoritative=True)
    # Second authoritative annotation on the same item raises.
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


@pytest.mark.django_db()
def test_get_progress_includes_awaiting_resolution(team):
    User = get_user_model()
    user1 = team.members.first()
    user2 = User.objects.create(username="r2", email="r2@e.com")
    MembershipFactory.create(team=team, user=user2)
    queue = AnnotationQueue.objects.create(team=team, name="Q", schema={}, created_by=user1, num_reviews_required=2)
    session = ExperimentSessionFactory.create(team=team, chat__team=team)
    item = AnnotationItem.objects.create(queue=queue, team=team, item_type=AnnotationItemType.SESSION, session=session)
    Annotation.objects.create(item=item, team=team, reviewer=user1, data={}, status=AnnotationStatus.SUBMITTED)
    Annotation.objects.create(item=item, team=team, reviewer=user2, data={}, status=AnnotationStatus.SUBMITTED)
    item.refresh_from_db()
    assert item.status == AnnotationItemStatus.AWAITING_RESOLUTION

    progress = queue.get_progress()

    assert progress["awaiting_resolution_items"] == 1
    assert progress["completed_items"] == 0


# ===== num_reviews_required recompute (resync_items) =====


def _make_session_item(queue, team):
    session = ExperimentSessionFactory.create(team=team, chat__team=team)
    return AnnotationItem.objects.create(queue=queue, team=team, item_type=AnnotationItemType.SESSION, session=session)


@pytest.mark.django_db()
def test_resync_raising_requirement_reopens_completed_item(team):
    """Raising the requirement reverts a completed single-review item to in_progress."""
    user = team.members.first()
    queue = AnnotationQueue.objects.create(team=team, name="Q", schema={}, created_by=user, num_reviews_required=1)
    item = _make_session_item(queue, team)
    Annotation.objects.create(item=item, team=team, reviewer=user, data={}, status=AnnotationStatus.SUBMITTED)
    item.refresh_from_db()
    assert item.status == AnnotationItemStatus.COMPLETED

    queue.num_reviews_required = 3
    queue.save()
    queue.resync_items()

    item.refresh_from_db()
    assert item.status == AnnotationItemStatus.IN_PROGRESS


@pytest.mark.django_db()
def test_resync_raising_requirement_clears_auto_assigned_authoritative(team):
    """Going multi-review clears auto-assigned authoritative flags (set_by is null)."""
    user = team.members.first()
    queue = AnnotationQueue.objects.create(team=team, name="Q", schema={}, created_by=user, num_reviews_required=1)
    item = _make_session_item(queue, team)
    ann = Annotation.objects.create(item=item, team=team, reviewer=user, data={}, status=AnnotationStatus.SUBMITTED)
    ann.refresh_from_db()
    assert ann.is_authoritative is True
    assert ann.authoritative_set_by is None

    queue.num_reviews_required = 3
    queue.save()
    queue.resync_items()

    ann.refresh_from_db()
    assert ann.is_authoritative is False
    assert ann.authoritative_set_at is None


@pytest.mark.django_db()
def test_resync_raising_requirement_preserves_human_set_authoritative(team):
    """A human-picked authoritative flag (set_by populated) survives a raise above 1."""
    User = get_user_model()
    user1 = team.members.first()
    user2 = User.objects.create(username="r2", email="r2@e.com")
    MembershipFactory.create(team=team, user=user2)
    queue = AnnotationQueue.objects.create(team=team, name="Q", schema={}, created_by=user1, num_reviews_required=2)
    item = _make_session_item(queue, team)
    ann1 = Annotation.objects.create(item=item, team=team, reviewer=user1, data={}, status=AnnotationStatus.SUBMITTED)
    Annotation.objects.create(item=item, team=team, reviewer=user2, data={}, status=AnnotationStatus.SUBMITTED)
    item.refresh_from_db()
    assert item.status == AnnotationItemStatus.AWAITING_RESOLUTION

    # Admin manually picks the authoritative annotation -> COMPLETED
    ann1.is_authoritative = True
    ann1.authoritative_set_by = user1
    ann1.authoritative_set_at = timezone.now()
    ann1.save(update_fields=["is_authoritative", "authoritative_set_by", "authoritative_set_at"])
    item.update_status()
    item.refresh_from_db()
    assert item.status == AnnotationItemStatus.COMPLETED

    queue.num_reviews_required = 3
    queue.save()
    queue.resync_items()

    ann1.refresh_from_db()
    assert ann1.is_authoritative is True  # human-picked flag preserved
    item.refresh_from_db()
    assert item.status == AnnotationItemStatus.IN_PROGRESS  # 2 reviews < 3 required


@pytest.mark.django_db()
def test_resync_lowering_requirement_keeps_unresolved_item_awaiting(team):
    """Lowering to 1 keeps a multi-review item with no authoritative pick awaiting resolution.

    The disagreement between the two reviews is unresolved, so completing it would be
    dishonest — it stays AWAITING_RESOLUTION until an admin picks an authoritative one.
    """
    User = get_user_model()
    user1 = team.members.first()
    user2 = User.objects.create(username="r2", email="r2@e.com")
    MembershipFactory.create(team=team, user=user2)
    queue = AnnotationQueue.objects.create(team=team, name="Q", schema={}, created_by=user1, num_reviews_required=2)
    item = _make_session_item(queue, team)
    Annotation.objects.create(item=item, team=team, reviewer=user1, data={}, status=AnnotationStatus.SUBMITTED)
    Annotation.objects.create(item=item, team=team, reviewer=user2, data={}, status=AnnotationStatus.SUBMITTED)
    item.refresh_from_db()
    assert item.status == AnnotationItemStatus.AWAITING_RESOLUTION

    queue.num_reviews_required = 1
    queue.save()
    queue.resync_items()

    item.refresh_from_db()
    assert item.status == AnnotationItemStatus.AWAITING_RESOLUTION


@pytest.mark.django_db()
def test_resync_lowering_requirement_completes_resolved_item(team):
    """Lowering to 1 completes a multi-review item that already has an authoritative pick."""
    User = get_user_model()
    user1 = team.members.first()
    user2 = User.objects.create(username="r2", email="r2@e.com")
    MembershipFactory.create(team=team, user=user2)
    queue = AnnotationQueue.objects.create(team=team, name="Q", schema={}, created_by=user1, num_reviews_required=2)
    item = _make_session_item(queue, team)
    ann1 = Annotation.objects.create(item=item, team=team, reviewer=user1, data={}, status=AnnotationStatus.SUBMITTED)
    Annotation.objects.create(item=item, team=team, reviewer=user2, data={}, status=AnnotationStatus.SUBMITTED)
    item.refresh_from_db()
    assert item.status == AnnotationItemStatus.AWAITING_RESOLUTION

    ann1.is_authoritative = True
    ann1.authoritative_set_by = user1
    ann1.authoritative_set_at = timezone.now()
    ann1.save(update_fields=["is_authoritative", "authoritative_set_by", "authoritative_set_at"])
    item.update_status()
    item.refresh_from_db()
    assert item.status == AnnotationItemStatus.COMPLETED

    queue.num_reviews_required = 1
    queue.save()
    queue.resync_items()

    ann1.refresh_from_db()
    item.refresh_from_db()
    assert ann1.is_authoritative is True  # human-picked auth kept (clear only runs when raising)
    assert item.status == AnnotationItemStatus.COMPLETED


@pytest.mark.django_db()
def test_resync_does_not_touch_flagged_items(team):
    """Flagged items are left untouched by resync, matching update_status."""
    user = team.members.first()
    queue = AnnotationQueue.objects.create(team=team, name="Q", schema={}, created_by=user, num_reviews_required=1)
    item = _make_session_item(queue, team)
    Annotation.objects.create(item=item, team=team, reviewer=user, data={}, status=AnnotationStatus.SUBMITTED)
    item.status = AnnotationItemStatus.FLAGGED
    item.save(update_fields=["status"])

    queue.num_reviews_required = 3
    queue.save()
    queue.resync_items()

    item.refresh_from_db()
    assert item.status == AnnotationItemStatus.FLAGGED


@pytest.mark.django_db()
def test_resync_recomputes_aggregates_after_clearing_authoritative(team):
    """Clearing authoritative flags refreshes stored aggregates (authoritative -> all-submitted)."""
    User = get_user_model()
    user1 = team.members.first()
    user2 = User.objects.create(username="r2", email="r2@e.com")
    MembershipFactory.create(team=team, user=user2)
    queue = AnnotationQueue.objects.create(
        team=team,
        name="Q",
        schema={"score": {"type": "int", "description": "Score", "ge": 1, "le": 5}},
        created_by=user1,
        num_reviews_required=1,
    )
    item = _make_session_item(queue, team)
    # First submission auto-marked authoritative (score=5).
    Annotation.objects.create(
        item=item, team=team, reviewer=user1, data={"score": 5}, status=AnnotationStatus.SUBMITTED
    )
    # Over-budget second submission stays non-authoritative (score=1).
    Annotation.objects.create(
        item=item, team=team, reviewer=user2, data={"score": 1}, status=AnnotationStatus.SUBMITTED
    )
    queue.refresh_from_db()
    # Aggregation uses only the authoritative annotation -> mean 5.
    assert queue.aggregate.aggregates["score"]["mean"] == 5

    queue.num_reviews_required = 3
    queue.save()
    queue.resync_items()

    queue.refresh_from_db()
    # Authoritative cleared -> aggregation falls back to all submitted (5, 1) -> mean 3.
    assert queue.aggregate.aggregates["score"]["mean"] == 3
