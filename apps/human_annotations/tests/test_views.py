import json

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from apps.human_annotations.aggregation import compute_aggregates_for_queue
from apps.human_annotations.models import (
    Annotation,
    AnnotationItemStatus,
    AnnotationQueue,
    AnnotationStatus,
)
from apps.utils.factories.human_annotations import (
    AnnotationItemFactory,
    AnnotationQueueFactory,
)
from apps.utils.factories.team import TeamWithUsersFactory

User = get_user_model()


@pytest.fixture()
def team_with_users(db):
    return TeamWithUsersFactory.create()


@pytest.fixture()
def user(team_with_users):
    return team_with_users.members.first()


@pytest.fixture()
def client(user):
    c = Client()
    c.force_login(user)
    return c


@pytest.fixture()
def queue(team_with_users, user):
    return AnnotationQueueFactory(team=team_with_users, created_by=user)


# ===== Queue CRUD =====


@pytest.mark.django_db()
def test_queue_home(client, team_with_users):
    url = reverse("human_annotations:queue_home", args=[team_with_users.slug])
    response = client.get(url)
    assert response.status_code == 200


@pytest.mark.django_db()
def test_queue_table(client, team_with_users, queue):
    url = reverse("human_annotations:queue_table", args=[team_with_users.slug])
    response = client.get(url)
    assert response.status_code == 200
    assert queue.name in response.content.decode()


@pytest.mark.django_db()
def test_create_queue(client, team_with_users):
    url = reverse("human_annotations:queue_new", args=[team_with_users.slug])
    data = {
        "name": "New Queue",
        "description": "A test queue",
        "schema": json.dumps({"score": {"type": "int", "description": "Score", "ge": 1, "le": 5}}),
        "num_reviews_required": 2,
    }
    response = client.post(url, data)
    assert response.status_code == 302
    assert AnnotationQueue.objects.filter(name="New Queue", team=team_with_users).exists()


@pytest.mark.django_db()
def test_create_queue_with_optional_field(client, team_with_users):
    """Creating a queue with required=false should persist and load correctly."""
    url = reverse("human_annotations:queue_new", args=[team_with_users.slug])
    schema = {
        "score": {"type": "int", "description": "Score"},
        "notes": {"type": "string", "description": "Notes", "required": False},
    }
    data = {
        "name": "Optional Fields Queue",
        "description": "",
        "schema": json.dumps(schema),
        "num_reviews_required": 1,
    }
    response = client.post(url, data)
    assert response.status_code == 302

    queue = AnnotationQueue.objects.get(name="Optional Fields Queue", team=team_with_users)
    assert queue.schema["notes"]["required"] is False

    # Load the edit page and verify the schema is in the context
    edit_url = reverse("human_annotations:queue_edit", args=[team_with_users.slug, queue.pk])
    response = client.get(edit_url)
    assert response.status_code == 200
    assert response.context["existing_schema"]["notes"]["required"] is False


@pytest.mark.django_db()
def test_edit_queue_saves_optional_field(client, team_with_users, queue):
    """Editing a queue to set required=false should persist via the update view."""
    edit_url = reverse("human_annotations:queue_edit", args=[team_with_users.slug, queue.pk])
    schema = {
        "score": {"type": "int", "description": "Score", "ge": 1, "le": 5},
        "comment": {"type": "string", "description": "Comment", "required": False},
    }
    response = client.post(
        edit_url,
        {
            "name": queue.name,
            "description": queue.description,
            "schema": json.dumps(schema),
            "num_reviews_required": queue.num_reviews_required,
        },
    )
    assert response.status_code == 302

    queue.refresh_from_db()
    assert queue.schema["comment"]["required"] is False

    # Verify the edit page renders the saved value in both context and HTML
    response = client.get(edit_url)
    assert response.context["existing_schema"]["comment"]["required"] is False
    # Check the json_script output in the rendered HTML
    html = response.content.decode()
    assert '"required": false' in html or '"required":false' in html


@pytest.mark.django_db()
def test_edit_queue_locks_fields_after_annotations(client, team_with_users, queue, user):
    """num_reviews_required should be disabled after annotations have started; schema stays editable for 'required'."""
    item = AnnotationItemFactory(queue=queue, team=team_with_users)
    Annotation.objects.create(
        item=item,
        team=team_with_users,
        reviewer=user,
        data={"quality_score": 4, "notes": "OK"},
        status=AnnotationStatus.SUBMITTED,
    )

    url = reverse("human_annotations:queue_edit", args=[team_with_users.slug, queue.pk])
    response = client.get(url)
    assert response.status_code == 200
    form = response.context["form"]
    assert form.fields["schema"].disabled is False
    assert form.fields["num_reviews_required"].disabled is True
    assert response.context["schema_locked"] is True


@pytest.mark.django_db()
def test_edit_locked_queue_allows_required_change(client, team_with_users, user):
    """Changing 'required' on schema fields should be allowed after annotations have started."""
    queue = AnnotationQueue.objects.create(
        team=team_with_users,
        name="Locked Queue",
        schema={
            "score": {"type": "int", "description": "Score", "ge": 1, "le": 5},
            "notes": {"type": "string", "description": "Notes"},
        },
        created_by=user,
    )
    item = AnnotationItemFactory(queue=queue, team=team_with_users)
    Annotation.objects.create(
        item=item,
        team=team_with_users,
        reviewer=user,
        data={"score": 4, "notes": "OK"},
        status=AnnotationStatus.SUBMITTED,
    )

    # Change 'notes' to optional — should succeed
    new_schema = {
        "score": {"type": "int", "description": "Score", "ge": 1, "le": 5},
        "notes": {"type": "string", "description": "Notes", "required": False},
    }
    url = reverse("human_annotations:queue_edit", args=[team_with_users.slug, queue.pk])
    response = client.post(
        url,
        {
            "name": queue.name,
            "description": "",
            "schema": json.dumps(new_schema),
            "num_reviews_required": queue.num_reviews_required,
        },
    )
    assert response.status_code == 302

    queue.refresh_from_db()
    assert queue.schema["notes"]["required"] is False


@pytest.mark.django_db()
def test_edit_locked_queue_rejects_structural_change(client, team_with_users, user):
    """Changing field structure (type, constraints, etc.) should be rejected after annotations."""
    queue = AnnotationQueue.objects.create(
        team=team_with_users,
        name="Locked Queue",
        schema={
            "score": {"type": "int", "description": "Score", "ge": 1, "le": 5},
        },
        created_by=user,
    )
    item = AnnotationItemFactory(queue=queue, team=team_with_users)
    Annotation.objects.create(
        item=item,
        team=team_with_users,
        reviewer=user,
        data={"score": 4},
        status=AnnotationStatus.SUBMITTED,
    )

    url = reverse("human_annotations:queue_edit", args=[team_with_users.slug, queue.pk])

    # Try changing the type from int to float
    bad_schema = {"score": {"type": "float", "description": "Score", "ge": 1, "le": 5}}
    response = client.post(
        url,
        {
            "name": queue.name,
            "description": "",
            "schema": json.dumps(bad_schema),
            "num_reviews_required": queue.num_reviews_required,
        },
    )
    assert response.status_code == 200  # re-renders form with error
    assert "Cannot change" in response.context["form"].errors["schema"][0]


@pytest.mark.django_db()
def test_edit_locked_queue_rejects_adding_field(client, team_with_users, user):
    """Adding a new field should be rejected after annotations."""
    queue = AnnotationQueue.objects.create(
        team=team_with_users,
        name="Locked Queue",
        schema={"score": {"type": "int", "description": "Score"}},
        created_by=user,
    )
    item = AnnotationItemFactory(queue=queue, team=team_with_users)
    Annotation.objects.create(
        item=item,
        team=team_with_users,
        reviewer=user,
        data={"score": 4},
        status=AnnotationStatus.SUBMITTED,
    )

    url = reverse("human_annotations:queue_edit", args=[team_with_users.slug, queue.pk])
    bad_schema = {
        "score": {"type": "int", "description": "Score"},
        "notes": {"type": "string", "description": "Notes"},
    }
    response = client.post(
        url,
        {
            "name": queue.name,
            "description": "",
            "schema": json.dumps(bad_schema),
            "num_reviews_required": queue.num_reviews_required,
        },
    )
    assert response.status_code == 200
    assert "Cannot add or remove" in response.context["form"].errors["schema"][0]


@pytest.mark.django_db()
def test_queue_detail(client, team_with_users, queue):
    url = reverse("human_annotations:queue_detail", args=[team_with_users.slug, queue.pk])
    response = client.get(url)
    assert response.status_code == 200
    assert queue.name in response.content.decode()


@pytest.mark.django_db()
def test_queue_detail_shows_aggregates(client, team_with_users, queue, user):
    item = AnnotationItemFactory(queue=queue, team=team_with_users)
    Annotation.objects.create(
        item=item,
        team=team_with_users,
        reviewer=user,
        data={"quality_score": 4, "notes": "Good"},
        status=AnnotationStatus.SUBMITTED,
    )

    # Compute aggregates
    compute_aggregates_for_queue(queue)

    url = reverse("human_annotations:queue_detail", args=[team_with_users.slug, queue.pk])
    response = client.get(url)
    assert response.status_code == 200
    content = response.content.decode()
    assert "Aggregate Scores" in content
    assert "quality_score" in content


@pytest.mark.django_db()
def test_queue_items_table(client, team_with_users, queue):
    AnnotationItemFactory(queue=queue, team=team_with_users)
    url = reverse("human_annotations:queue_items_table", args=[team_with_users.slug, queue.pk])
    response = client.get(url)
    assert response.status_code == 200


# ===== Assignee Management =====


@pytest.mark.django_db()
def test_manage_assignees_post(client, team_with_users, queue, user):
    url = reverse("human_annotations:queue_manage_assignees", args=[team_with_users.slug, queue.pk])
    response = client.post(url, {"assignees": [user.pk]})
    assert response.status_code == 302
    assert user in queue.assignees.all()


# ===== Annotator UI =====


@pytest.mark.django_db()
def test_annotate_queue_no_items(client, team_with_users, queue):
    url = reverse("human_annotations:annotate_queue", args=[team_with_users.slug, queue.pk])
    response = client.get(url)
    # Should redirect when no items to annotate
    assert response.status_code == 302


@pytest.mark.django_db()
def test_annotate_queue_with_item(client, team_with_users, queue):
    AnnotationItemFactory(queue=queue, team=team_with_users)
    url = reverse("human_annotations:annotate_queue", args=[team_with_users.slug, queue.pk])
    response = client.get(url)
    assert response.status_code == 200


@pytest.mark.django_db()
def test_submit_annotation(client, team_with_users, queue, user):
    item = AnnotationItemFactory(queue=queue, team=team_with_users)
    url = reverse(
        "human_annotations:submit_annotation",
        args=[team_with_users.slug, queue.pk, item.pk],
    )
    data = {"quality_score": 4, "notes": "Looks good"}
    response = client.post(url, data)
    assert response.status_code == 302

    annotation = Annotation.objects.get(item=item, reviewer=user)
    assert annotation.data["quality_score"] == 4
    assert annotation.status == AnnotationStatus.SUBMITTED

    item.refresh_from_db()
    assert item.review_count == 1
    assert item.status == AnnotationItemStatus.COMPLETED


@pytest.mark.django_db()
def test_submit_annotation_duplicate(client, team_with_users, queue, user):
    item = AnnotationItemFactory(queue=queue, team=team_with_users)
    Annotation.objects.create(
        item=item,
        team=team_with_users,
        reviewer=user,
        data={"quality_score": 3, "notes": "First"},
        status=AnnotationStatus.SUBMITTED,
    )
    url = reverse(
        "human_annotations:submit_annotation",
        args=[team_with_users.slug, queue.pk, item.pk],
    )
    response = client.post(url, {"quality_score": 5, "notes": "Second"})
    assert response.status_code == 302
    # Should not create a duplicate
    assert Annotation.objects.filter(item=item, reviewer=user).count() == 1


@pytest.mark.django_db()
def test_skip_item(client, team_with_users, queue):
    item1 = AnnotationItemFactory(queue=queue, team=team_with_users)
    item2 = AnnotationItemFactory(queue=queue, team=team_with_users)
    # Without skip, should get item1 (oldest)
    url = reverse("human_annotations:annotate_queue", args=[team_with_users.slug, queue.pk])
    response = client.get(url)
    assert response.status_code == 200
    assert response.context["item"].pk == item1.pk

    # With skip=item1, should get item2
    response = client.get(url, {"skip": item1.pk})
    assert response.status_code == 200
    assert response.context["item"].pk == item2.pk


@pytest.mark.django_db()
def test_annotate_item_specific(client, team_with_users, queue):
    item = AnnotationItemFactory(queue=queue, team=team_with_users)
    url = reverse(
        "human_annotations:annotate_item",
        args=[team_with_users.slug, queue.pk, item.pk],
    )
    response = client.get(url)
    assert response.status_code == 200
    assert response.context["item"].pk == item.pk


@pytest.mark.django_db()
def test_annotate_item_already_annotated(client, team_with_users, queue, user):
    item = AnnotationItemFactory(queue=queue, team=team_with_users)
    Annotation.objects.create(
        item=item,
        team=team_with_users,
        reviewer=user,
        data={"quality_score": 3},
        status=AnnotationStatus.SUBMITTED,
    )
    url = reverse(
        "human_annotations:annotate_item",
        args=[team_with_users.slug, queue.pk, item.pk],
    )
    response = client.get(url)
    assert response.status_code == 200
    assert response.context["can_annotate"] is False
    assert response.context["form"] is None
    assert len(response.context["annotations"]) == 1
    assert response.context["annotations"][0]["reviewer"] == user


@pytest.mark.django_db()
def test_annotate_item_non_assignee_can_view(client, team_with_users, queue):
    """Non-assignees can view item content but not the annotation form."""
    other_user = User.objects.create_user(username="other", password="test")
    queue.assignees.add(other_user)
    item = AnnotationItemFactory(queue=queue, team=team_with_users)
    url = reverse(
        "human_annotations:annotate_item",
        args=[team_with_users.slug, queue.pk, item.pk],
    )
    response = client.get(url)
    assert response.status_code == 200
    assert response.context["can_annotate"] is False
    assert response.context["form"] is None
    assert response.context["annotations"] == []


@pytest.mark.django_db()
def test_flag_item_with_reason(client, team_with_users, queue, user):
    item = AnnotationItemFactory(queue=queue, team=team_with_users)
    url = reverse(
        "human_annotations:flag_item",
        args=[team_with_users.slug, queue.pk, item.pk],
    )
    response = client.post(url, {"flag_reason": "Content seems wrong"})
    assert response.status_code == 302
    item.refresh_from_db()
    assert item.status == AnnotationItemStatus.FLAGGED
    assert len(item.flags) == 1
    assert item.flags[0]["reason"] == "Content seems wrong"
    assert item.flags[0]["user_id"] == user.pk
    assert item.flags[0]["user"] != ""


@pytest.mark.django_db()
def test_unflag_item(client, team_with_users, queue):
    item = AnnotationItemFactory(queue=queue, team=team_with_users)
    item.status = AnnotationItemStatus.FLAGGED
    item.flags = [{"user": "Test", "user_id": 1, "reason": "Bad data", "timestamp": "2024-01-01T00:00:00"}]
    item.save(update_fields=["status", "flags"])

    url = reverse(
        "human_annotations:unflag_item",
        args=[team_with_users.slug, queue.pk, item.pk],
    )
    response = client.post(url)
    assert response.status_code == 302
    item.refresh_from_db()
    assert item.status == AnnotationItemStatus.PENDING
    assert item.flags == []


@pytest.mark.django_db()
def test_unflag_item_with_reviews(client, team_with_users, queue, user):
    """Unflagging an item with existing reviews should set status to IN_PROGRESS."""
    item = AnnotationItemFactory(queue=queue, team=team_with_users)
    Annotation.objects.create(
        item=item,
        team=team_with_users,
        reviewer=user,
        data={"quality_score": 3, "notes": "OK"},
        status=AnnotationStatus.SUBMITTED,
    )
    item.status = AnnotationItemStatus.FLAGGED
    item.flags = [{"user": "Test", "user_id": 1, "reason": "Needs check", "timestamp": "2024-01-01T00:00:00"}]
    item.save(update_fields=["status", "flags"])

    url = reverse(
        "human_annotations:unflag_item",
        args=[team_with_users.slug, queue.pk, item.pk],
    )
    response = client.post(url)
    assert response.status_code == 302
    item.refresh_from_db()
    assert item.status == AnnotationItemStatus.COMPLETED  # num_reviews_required=1
    assert item.flags == []


@pytest.mark.django_db()
def test_flag_item_htmx(client, team_with_users, queue):
    item = AnnotationItemFactory(queue=queue, team=team_with_users)
    url = reverse(
        "human_annotations:flag_item",
        args=[team_with_users.slug, queue.pk, item.pk],
    )
    response = client.post(url, HTTP_HX_REQUEST="true")
    assert response.status_code == 204
    assert "HX-Redirect" in response
    item.refresh_from_db()
    assert item.status == AnnotationItemStatus.FLAGGED


# ===== Export =====


@pytest.mark.django_db()
def test_export_csv(client, team_with_users, queue, user):
    item = AnnotationItemFactory(queue=queue, team=team_with_users)
    Annotation.objects.create(
        item=item,
        team=team_with_users,
        reviewer=user,
        data={"quality_score": 5, "notes": "Great"},
        status=AnnotationStatus.SUBMITTED,
    )
    url = reverse("human_annotations:queue_export", args=[team_with_users.slug, queue.pk])
    response = client.get(url, {"format": "csv"})
    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv"
    content = response.content.decode()
    assert "quality_score" in content


@pytest.mark.django_db()
def test_export_jsonl(client, team_with_users, queue, user):
    item = AnnotationItemFactory(queue=queue, team=team_with_users)
    Annotation.objects.create(
        item=item,
        team=team_with_users,
        reviewer=user,
        data={"quality_score": 5, "notes": "Great"},
        status=AnnotationStatus.SUBMITTED,
    )
    url = reverse("human_annotations:queue_export", args=[team_with_users.slug, queue.pk])
    response = client.get(url, {"format": "jsonl"})
    assert response.status_code == 200
    assert response["Content-Type"] == "application/jsonl"
    record = json.loads(response.content.decode().strip())
    assert record["annotation"]["quality_score"] == 5


# ===== Multi-Review =====


@pytest.mark.django_db()
def test_multi_review_second_user_can_annotate(team_with_users):
    """After user1 annotates all items, user2 should still see them when num_reviews_required > 1."""
    user1 = team_with_users.members.first()
    user2 = team_with_users.members.last()
    assert user1 != user2

    queue = AnnotationQueueFactory(team=team_with_users, created_by=user1, num_reviews_required=2)
    item = AnnotationItemFactory(queue=queue, team=team_with_users)

    # User 1 submits annotation
    Annotation.objects.create(
        item=item,
        team=team_with_users,
        reviewer=user1,
        data={"quality_score": 4, "notes": "Good"},
        status=AnnotationStatus.SUBMITTED,
    )
    item.refresh_from_db()
    assert item.review_count == 1
    assert item.status == AnnotationItemStatus.IN_PROGRESS

    # User 2 should be able to see and annotate the item
    client2 = Client()
    client2.force_login(user2)
    url = reverse("human_annotations:annotate_queue", args=[team_with_users.slug, queue.pk])
    response = client2.get(url)
    assert response.status_code == 200
    assert response.context["item"].pk == item.pk


@pytest.mark.django_db()
def test_multi_review_item_completed_after_enough_reviews(team_with_users):
    """Item should only be COMPLETED after reaching num_reviews_required."""
    user1 = team_with_users.members.first()
    user2 = team_with_users.members.last()

    queue = AnnotationQueueFactory(team=team_with_users, created_by=user1, num_reviews_required=2)
    item = AnnotationItemFactory(queue=queue, team=team_with_users)

    # First review
    Annotation.objects.create(
        item=item,
        team=team_with_users,
        reviewer=user1,
        data={"quality_score": 4, "notes": "OK"},
        status=AnnotationStatus.SUBMITTED,
    )
    item.refresh_from_db()
    assert item.status == AnnotationItemStatus.IN_PROGRESS

    # Second review - should complete
    Annotation.objects.create(
        item=item,
        team=team_with_users,
        reviewer=user2,
        data={"quality_score": 5, "notes": "Great"},
        status=AnnotationStatus.SUBMITTED,
    )
    item.refresh_from_db()
    assert item.review_count == 2
    assert item.status == AnnotationItemStatus.COMPLETED


@pytest.mark.django_db()
def test_progress_accounts_for_multiple_reviews(team_with_users):
    """Progress should reflect review-level progress, not just item completion."""
    user1 = team_with_users.members.first()

    queue = AnnotationQueueFactory(team=team_with_users, created_by=user1, num_reviews_required=2)
    AnnotationItemFactory(queue=queue, team=team_with_users)
    item2 = AnnotationItemFactory(queue=queue, team=team_with_users)

    # No reviews yet
    progress = queue.get_progress()
    assert progress["total_items"] == 2
    assert progress["total_reviews_needed"] == 4
    assert progress["reviews_done"] == 0
    assert progress["percent"] == 0

    # One review on item2
    Annotation.objects.create(
        item=item2,
        team=team_with_users,
        reviewer=user1,
        data={"quality_score": 3, "notes": "OK"},
        status=AnnotationStatus.SUBMITTED,
    )
    progress = queue.get_progress()
    assert progress["reviews_done"] == 1
    assert progress["percent"] == 25
