import csv
import io
import json

import pytest
from django.test import Client
from django.urls import reverse

from apps.human_annotations.models import (
    Annotation,
    AnnotationItem,
    AnnotationItemStatus,
    AnnotationItemType,
    AnnotationQueue,
)
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def team():
    return TeamWithUsersFactory()


@pytest.mark.django_db()
def test_full_annotation_workflow(team):
    """End-to-end: create queue, add items, annotate with two users, verify completion, export."""
    user1 = team.members.first()
    user2 = team.members.last()
    assert user1 != user2

    # 1. Create queue with schema, 2 reviews required
    queue = AnnotationQueue.objects.create(
        team=team,
        name="Q1 Audit",
        schema={
            "score": {"type": "int", "description": "Score 1-5", "ge": 1, "le": 5},
            "feedback": {"type": "string", "description": "Feedback"},
        },
        created_by=user1,
        num_reviews_required=2,
    )
    queue.assignees.add(user1, user2)

    # 2. Add items from sessions
    sessions = [ExperimentSessionFactory(team=team, chat__team=team) for _ in range(3)]
    for session in sessions:
        AnnotationItem.objects.create(
            queue=queue,
            team=team,
            item_type=AnnotationItemType.SESSION,
            session=session,
        )

    assert queue.items.count() == 3
    progress = queue.get_progress()
    assert progress["total_items"] == 3
    assert progress["completed_items"] == 0
    assert progress["percent"] == 0

    # 3. User1 annotates item 1 -> IN_PROGRESS
    item1 = queue.items.first()
    Annotation.objects.create(
        item=item1,
        team=team,
        reviewer=user1,
        data={"score": 4, "feedback": "Good"},
    )
    item1.refresh_from_db()
    assert item1.status == AnnotationItemStatus.IN_PROGRESS
    assert item1.review_count == 1

    # 4. User2 annotates item 1 -> COMPLETED (meets num_reviews_required=2)
    Annotation.objects.create(
        item=item1,
        team=team,
        reviewer=user2,
        data={"score": 5, "feedback": "Great"},
    )
    item1.refresh_from_db()
    assert item1.status == AnnotationItemStatus.COMPLETED
    assert item1.review_count == 2

    # 5. Verify progress
    progress = queue.get_progress()
    assert progress["completed_items"] == 1
    assert progress["total_items"] == 3
    assert progress["reviews_done"] == 2
    assert progress["total_reviews_needed"] == 6

    # 6. Export CSV via view
    client = Client()
    client.force_login(user1)
    url = reverse("human_annotations:queue_export", args=[team.slug, queue.pk])
    response = client.get(url, {"format": "csv"})
    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv"
    content = response.content.decode()
    reader = csv.DictReader(io.StringIO(content))
    rows = list(reader)
    assert len(rows) == 2  # 2 annotations on item1
    scores = {row["score"] for row in rows}
    assert scores == {"4", "5"}

    # 7. Export JSONL
    response = client.get(url, {"format": "jsonl"})
    assert response.status_code == 200
    lines = response.content.decode().strip().split("\n")
    assert len(lines) == 2
    data = json.loads(lines[0])
    assert "annotation" in data
    assert data["annotation"]["score"] in (4, 5)


@pytest.mark.django_db()
def test_flag_unflag_workflow(team):
    """Flag an item, verify it stays flagged through annotations, unflag to resume."""
    user = team.members.first()
    queue = AnnotationQueue.objects.create(
        team=team,
        name="Flag Test",
        schema={"score": {"type": "int", "description": "Score", "ge": 1, "le": 5}},
        created_by=user,
        num_reviews_required=1,
    )
    session = ExperimentSessionFactory(team=team, chat__team=team)
    item = AnnotationItem.objects.create(
        queue=queue,
        team=team,
        item_type=AnnotationItemType.SESSION,
        session=session,
    )

    # Flag the item
    client = Client()
    client.force_login(user)
    flag_url = reverse("human_annotations:flag_item", args=[team.slug, queue.pk, item.pk])
    response = client.post(flag_url, {"flag_reason": "Needs review"})
    assert response.status_code == 302
    item.refresh_from_db()
    assert item.status == AnnotationItemStatus.FLAGGED

    # Flagged items should not appear in annotate queue
    annotate_url = reverse("human_annotations:annotate_queue", args=[team.slug, queue.pk])
    response = client.get(annotate_url)
    assert response.status_code == 302  # no items to annotate

    # Unflag the item
    unflag_url = reverse("human_annotations:unflag_item", args=[team.slug, queue.pk, item.pk])
    response = client.post(unflag_url)
    assert response.status_code == 302
    item.refresh_from_db()
    assert item.status == AnnotationItemStatus.PENDING

    # Now item should appear in annotate queue
    response = client.get(annotate_url)
    assert response.status_code == 200
