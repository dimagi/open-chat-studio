import csv
import io
import json
from datetime import UTC, datetime

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client
from django.urls import reverse

from apps.channels.models import ChannelPlatform
from apps.human_annotations.aggregation import compute_aggregates_for_queue
from apps.human_annotations.models import (
    Annotation,
    AnnotationItem,
    AnnotationItemStatus,
    AnnotationQueue,
    AnnotationStatus,
)
from apps.human_annotations.tables import AnnotationSessionsSelectionTable
from apps.human_annotations.views.export_views import ExportAnnotations
from apps.teams.backends import ANNOTATION_REVIEWER_GROUP
from apps.teams.models import Flag
from apps.utils.factories.evaluations import EvaluationDatasetFactory, EvaluationMessageFactory
from apps.utils.factories.experiment import ChatMessageFactory, ExperimentSessionFactory
from apps.utils.factories.human_annotations import (
    AnnotationItemFactory,
    AnnotationQueueFactory,
)
from apps.utils.factories.team import MembershipFactory, TeamWithUsersFactory
from apps.utils.factories.user import UserFactory

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
    return AnnotationQueueFactory.create(team=team_with_users, created_by=user)


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
def test_create_queue_duplicate_name_shows_form_error(client, team_with_users, queue):
    """Creating a queue with a name that already exists for the team returns a form error, not a 500."""
    url = reverse("human_annotations:queue_new", args=[team_with_users.slug])
    data = {
        "name": queue.name,
        "description": "A test queue",
        "schema": json.dumps({"score": {"type": "int", "description": "Score", "ge": 1, "le": 5}}),
        "num_reviews_required": 1,
    }
    response = client.post(url, data)
    assert response.status_code == 200
    assert not response.context["form"].is_valid()
    assert "already exists" in str(response.context["form"].errors)
    assert AnnotationQueue.objects.filter(name=queue.name, team=team_with_users).count() == 1


@pytest.mark.django_db()
def test_edit_queue_duplicate_name_shows_form_error(client, team_with_users, queue, user):
    """Renaming a queue to collide with another queue's name returns a form error, not a 500."""
    other = AnnotationQueueFactory.create(team=team_with_users, created_by=user, name="Other Queue")
    edit_url = reverse("human_annotations:queue_edit", args=[team_with_users.slug, other.pk])
    response = client.post(
        edit_url,
        {
            "name": queue.name,
            "description": other.description,
            "schema": json.dumps(other.schema),
            "num_reviews_required": other.num_reviews_required,
        },
    )
    assert response.status_code == 200
    assert not response.context["form"].is_valid()
    assert "already exists" in str(response.context["form"].errors)
    other.refresh_from_db()
    assert other.name == "Other Queue"


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
def test_edit_queue_schema_locked_but_num_reviews_editable_after_annotations(client, team_with_users, queue, user):
    """After annotations start the schema builder is locked, but num_reviews_required stays editable."""
    item = AnnotationItemFactory.create(queue=queue, team=team_with_users)
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
    assert form.fields["num_reviews_required"].disabled is False
    assert response.context["schema_locked"] is True
    assert response.context["annotations_started"] is True


@pytest.mark.django_db()
def test_edit_queue_change_num_reviews_recomputes_item_statuses(client, team_with_users, queue, user):
    """Changing num_reviews_required via the edit view recomputes existing item statuses."""
    item = AnnotationItemFactory.create(queue=queue, team=team_with_users)
    Annotation.objects.create(
        item=item,
        team=team_with_users,
        reviewer=user,
        data={"quality_score": 4, "notes": "OK"},
        status=AnnotationStatus.SUBMITTED,
    )
    item.refresh_from_db()
    assert item.status == AnnotationItemStatus.COMPLETED  # queue default num_reviews_required == 1

    url = reverse("human_annotations:queue_edit", args=[team_with_users.slug, queue.pk])
    response = client.post(
        url,
        {
            "name": queue.name,
            "description": queue.description,
            "schema": json.dumps(queue.schema),
            "num_reviews_required": 3,
        },
    )
    assert response.status_code == 302

    queue.refresh_from_db()
    assert queue.num_reviews_required == 3
    item.refresh_from_db()
    assert item.status == AnnotationItemStatus.IN_PROGRESS


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
    item = AnnotationItemFactory.create(queue=queue, team=team_with_users)
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
    item = AnnotationItemFactory.create(queue=queue, team=team_with_users)
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
    item = AnnotationItemFactory.create(queue=queue, team=team_with_users)
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
def test_queue_detail_shows_aggregates(client, team_with_users, queue, user):
    item = AnnotationItemFactory.create(queue=queue, team=team_with_users)
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
    AnnotationItemFactory.create(queue=queue, team=team_with_users)
    url = reverse("human_annotations:queue_items_table", args=[team_with_users.slug, queue.pk])
    response = client.get(url)
    assert response.status_code == 200


@pytest.mark.django_db()
def test_queue_items_table_filters_by_status(client, team_with_users, queue):
    pending_item = AnnotationItemFactory.create(queue=queue, team=team_with_users, status=AnnotationItemStatus.PENDING)
    completed_item = AnnotationItemFactory.create(
        queue=queue, team=team_with_users, status=AnnotationItemStatus.COMPLETED
    )
    url = reverse("human_annotations:queue_items_table", args=[team_with_users.slug, queue.pk])

    # Filter by pending using dynamic filter params
    response = client.get(
        url,
        {"filter_0_column": "status", "filter_0_operator": "any of", "filter_0_value": '["Pending"]'},
    )
    assert response.status_code == 200
    content = response.content.decode()
    assert str(pending_item.session.external_id) in content
    assert str(completed_item.session.external_id) not in content

    # Filter by completed
    response = client.get(
        url,
        {"filter_0_column": "status", "filter_0_operator": "any of", "filter_0_value": '["Completed"]'},
    )
    assert response.status_code == 200
    content = response.content.decode()
    assert str(completed_item.session.external_id) in content
    assert str(pending_item.session.external_id) not in content

    # No filter - should contain both
    response = client.get(url)
    assert response.status_code == 200
    content = response.content.decode()
    assert str(pending_item.session.external_id) in content
    assert str(completed_item.session.external_id) in content


@pytest.mark.django_db()
def test_queue_items_table_filters_by_reviewer(client, team_with_users, queue, user):
    item1 = AnnotationItemFactory.create(queue=queue, team=team_with_users)
    item2 = AnnotationItemFactory.create(queue=queue, team=team_with_users)
    Annotation.objects.create(
        item=item1,
        team=team_with_users,
        reviewer=user,
        data={},
        status=AnnotationStatus.SUBMITTED,
    )

    url = reverse("human_annotations:queue_items_table", args=[team_with_users.slug, queue.pk])

    # Filter by reviewer
    response = client.get(
        url,
        {"filter_0_column": "reviewer", "filter_0_operator": "any of", "filter_0_value": f'["{user.pk}"]'},
    )
    assert response.status_code == 200
    content = response.content.decode()
    assert str(item1.session.external_id) in content
    assert str(item2.session.external_id) not in content


@pytest.mark.django_db()
def test_queue_items_table_filters_by_session_id(client, team_with_users, queue):
    item1 = AnnotationItemFactory.create(queue=queue, team=team_with_users)
    item2 = AnnotationItemFactory.create(queue=queue, team=team_with_users)
    target_id = str(item1.session.external_id)

    url = reverse("human_annotations:queue_items_table", args=[team_with_users.slug, queue.pk])
    response = client.get(
        url,
        {"filter_0_column": "session_id", "filter_0_operator": "equals", "filter_0_value": target_id},
    )
    assert response.status_code == 200
    content = response.content.decode()
    assert target_id in content
    assert str(item2.session.external_id) not in content


@pytest.mark.django_db()
def test_queue_detail_has_filter_context(client, team_with_users, queue):
    url = reverse("human_annotations:queue_detail", args=[team_with_users.slug, queue.pk])
    response = client.get(url)
    assert response.status_code == 200
    assert "df_filter_columns" in response.context
    assert "df_table_type" in response.context


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
    AnnotationItemFactory.create(queue=queue, team=team_with_users)
    url = reverse("human_annotations:annotate_queue", args=[team_with_users.slug, queue.pk])
    response = client.get(url)
    assert response.status_code == 200


@pytest.mark.django_db()
def test_submit_annotation(client, team_with_users, queue, user):
    item = AnnotationItemFactory.create(queue=queue, team=team_with_users)
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
    item = AnnotationItemFactory.create(queue=queue, team=team_with_users)
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
    item1 = AnnotationItemFactory.create(queue=queue, team=team_with_users)
    item2 = AnnotationItemFactory.create(queue=queue, team=team_with_users)
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
    item = AnnotationItemFactory.create(queue=queue, team=team_with_users)
    url = reverse(
        "human_annotations:annotate_item",
        args=[team_with_users.slug, queue.pk, item.pk],
    )
    response = client.get(url)
    assert response.status_code == 200
    assert response.context["item"].pk == item.pk


@pytest.mark.django_db()
def test_annotate_item_already_annotated(client, team_with_users, queue, user):
    item = AnnotationItemFactory.create(queue=queue, team=team_with_users)
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
    item = AnnotationItemFactory.create(queue=queue, team=team_with_users)
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
    item = AnnotationItemFactory.create(queue=queue, team=team_with_users)
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
    item = AnnotationItemFactory.create(queue=queue, team=team_with_users)
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
    item = AnnotationItemFactory.create(queue=queue, team=team_with_users)
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
    item = AnnotationItemFactory.create(queue=queue, team=team_with_users)
    url = reverse(
        "human_annotations:flag_item",
        args=[team_with_users.slug, queue.pk, item.pk],
    )
    response = client.post(url, HTTP_HX_REQUEST="true")
    assert response.status_code == 204
    assert "HX-Redirect" in response
    item.refresh_from_db()
    assert item.status == AnnotationItemStatus.FLAGGED


# ===== Edit annotation =====


@pytest.mark.django_db()
def test_edit_annotation_updates_data_and_recomputes_aggregates(client, team_with_users, queue, user):
    item = AnnotationItemFactory.create(queue=queue, team=team_with_users)
    annotation = Annotation.objects.create(
        item=item,
        team=team_with_users,
        reviewer=user,
        data={"quality_score": 3, "notes": "Initial"},
        status=AnnotationStatus.SUBMITTED,
    )
    # Creation triggered aggregate compute with quality_score=3
    queue.refresh_from_db()
    assert queue.aggregate.aggregates["quality_score"]["mean"] == 3

    url = reverse(
        "human_annotations:edit_annotation",
        args=[team_with_users.slug, queue.pk, item.pk, annotation.pk],
    )
    response = client.post(url, {"quality_score": 5, "notes": "Updated"})
    assert response.status_code == 302

    annotation.refresh_from_db()
    assert annotation.data == {"quality_score": 5, "notes": "Updated"}

    queue.refresh_from_db()
    assert queue.aggregate.aggregates["quality_score"]["mean"] == 5


@pytest.mark.django_db()
def test_edit_annotation_forbidden_for_other_user(client, team_with_users, queue):
    other_user = team_with_users.members.exclude(id=team_with_users.members.first().id).first()
    item = AnnotationItemFactory.create(queue=queue, team=team_with_users)
    annotation = Annotation.objects.create(
        item=item,
        team=team_with_users,
        reviewer=other_user,
        data={"quality_score": 2, "notes": "Theirs"},
        status=AnnotationStatus.SUBMITTED,
    )
    url = reverse(
        "human_annotations:edit_annotation",
        args=[team_with_users.slug, queue.pk, item.pk, annotation.pk],
    )
    response = client.post(url, {"quality_score": 5, "notes": "Hijacked"})
    assert response.status_code == 403

    annotation.refresh_from_db()
    assert annotation.data == {"quality_score": 2, "notes": "Theirs"}


@pytest.mark.django_db()
def test_edit_annotation_works_after_item_completed(client, team_with_users, queue, user):
    item = AnnotationItemFactory.create(queue=queue, team=team_with_users)
    annotation = Annotation.objects.create(
        item=item,
        team=team_with_users,
        reviewer=user,
        data={"quality_score": 3, "notes": "First"},
        status=AnnotationStatus.SUBMITTED,
    )
    item.refresh_from_db()
    assert item.status == AnnotationItemStatus.COMPLETED  # queue.num_reviews_required == 1

    url = reverse(
        "human_annotations:edit_annotation",
        args=[team_with_users.slug, queue.pk, item.pk, annotation.pk],
    )
    response = client.post(url, {"quality_score": 4, "notes": "Updated"})
    assert response.status_code == 302
    annotation.refresh_from_db()
    assert annotation.data == {"quality_score": 4, "notes": "Updated"}


# ===== Export =====

# ----- _pivot_annotations -----


def _pivot(queue):
    """Build the same querysets ExportAnnotations.get() does and run the pivot."""
    annotations = Annotation.objects.filter(item__queue=queue, status=AnnotationStatus.SUBMITTED)
    flagged_items = AnnotationItem.objects.filter(queue=queue, status=AnnotationItemStatus.FLAGGED)
    return ExportAnnotations()._pivot_annotations(annotations, flagged_items, list(queue.schema.keys()))


@pytest.mark.django_db()
def test_pivot_single_annotator_single_field():
    queue = AnnotationQueueFactory.create(schema={"quality_score": {"type": "int", "description": "Quality"}})
    item = AnnotationItemFactory.create(queue=queue)
    alice = UserFactory.create(username="alice@example.com", email="alice@example.com")
    Annotation.objects.create(
        item=item, team=queue.team, reviewer=alice, data={"quality_score": 5}, status=AnnotationStatus.SUBMITTED
    )

    fieldnames, rows = _pivot(queue)

    assert fieldnames == [
        "item_id",
        "item_type",
        "session_id",
        "flagged",
        "flags",
        "field",
        "authoritative_annotator",
        "annotated_at",
        "alice@example.com",
    ]
    assert len(rows) == 1
    row = rows[0]
    assert row["item_id"] == item.pk
    assert row["item_type"] == item.item_type
    assert row["field"] == "quality_score"
    assert row["flagged"] is False
    assert row["alice@example.com"] == 5


@pytest.mark.django_db()
def test_pivot_multiple_schema_fields_produces_one_row_per_field():
    queue = AnnotationQueueFactory.create()  # default schema: quality_score, notes
    item = AnnotationItemFactory.create(queue=queue)
    alice = UserFactory.create(username="alice@example.com", email="alice@example.com")
    Annotation.objects.create(
        item=item,
        team=queue.team,
        reviewer=alice,
        data={"quality_score": 5, "notes": "Great"},
        status=AnnotationStatus.SUBMITTED,
    )

    _, rows = _pivot(queue)

    assert len(rows) == 2
    rows_by_field = {r["field"]: r for r in rows}
    assert rows_by_field.keys() == {"quality_score", "notes"}
    assert rows_by_field["quality_score"]["alice@example.com"] == 5
    assert rows_by_field["notes"]["alice@example.com"] == "Great"


@pytest.mark.parametrize(
    ("alice_score", "bob_score"),
    [
        pytest.param(5, 5, id="agreeing"),
        pytest.param(5, 2, id="disagreeing"),
    ],
)
@pytest.mark.django_db()
def test_pivot_two_annotators_values_attributed_to_correct_columns(alice_score, bob_score):
    queue = AnnotationQueueFactory.create(schema={"quality_score": {"type": "int", "description": "Quality"}})
    item = AnnotationItemFactory.create(queue=queue)
    alice = UserFactory.create(username="alice@example.com", email="alice@example.com")
    bob = UserFactory.create(username="bob@example.com", email="bob@example.com")
    Annotation.objects.create(
        item=item,
        team=queue.team,
        reviewer=alice,
        data={"quality_score": alice_score},
        status=AnnotationStatus.SUBMITTED,
    )
    Annotation.objects.create(
        item=item, team=queue.team, reviewer=bob, data={"quality_score": bob_score}, status=AnnotationStatus.SUBMITTED
    )

    _, rows = _pivot(queue)

    assert len(rows) == 1
    row = rows[0]
    assert row["alice@example.com"] == alice_score
    assert row["bob@example.com"] == bob_score


@pytest.mark.django_db()
def test_pivot_three_annotators_each_value_attributed_to_own_column():
    queue = AnnotationQueueFactory.create(schema={"quality_score": {"type": "int", "description": "Quality"}})
    item = AnnotationItemFactory.create(queue=queue)
    alice = UserFactory.create(username="alice@example.com", email="alice@example.com")
    bob = UserFactory.create(username="bob@example.com", email="bob@example.com")
    carol = UserFactory.create(username="carol@example.com", email="carol@example.com")
    Annotation.objects.create(
        item=item, team=queue.team, reviewer=alice, data={"quality_score": 5}, status=AnnotationStatus.SUBMITTED
    )
    Annotation.objects.create(
        item=item, team=queue.team, reviewer=bob, data={"quality_score": 3}, status=AnnotationStatus.SUBMITTED
    )
    Annotation.objects.create(
        item=item, team=queue.team, reviewer=carol, data={"quality_score": 1}, status=AnnotationStatus.SUBMITTED
    )

    fieldnames, rows = _pivot(queue)

    assert fieldnames[8:] == ["alice@example.com", "bob@example.com", "carol@example.com"]
    assert len(rows) == 1
    row = rows[0]
    assert row["alice@example.com"] == 5
    assert row["bob@example.com"] == 3
    assert row["carol@example.com"] == 1


@pytest.mark.django_db()
def test_pivot_authoritative_annotator_populated_when_pick_set():
    queue = AnnotationQueueFactory.create(
        schema={"quality_score": {"type": "int", "description": "Quality"}}, num_reviews_required=1
    )
    item = AnnotationItemFactory.create(queue=queue)
    alice = UserFactory.create(username="alice@example.com", email="alice@example.com")
    # num_reviews_required=1 means a single submitted review auto-marks itself authoritative.
    Annotation.objects.create(
        item=item, team=queue.team, reviewer=alice, data={"quality_score": 5}, status=AnnotationStatus.SUBMITTED
    )

    _, rows = _pivot(queue)

    assert rows[0]["authoritative_annotator"] == "alice@example.com"


@pytest.mark.django_db()
def test_pivot_authoritative_annotator_blank_when_no_pick_set():
    queue = AnnotationQueueFactory.create(
        schema={"quality_score": {"type": "int", "description": "Quality"}}, num_reviews_required=2
    )
    item = AnnotationItemFactory.create(queue=queue)
    alice = UserFactory.create(username="alice@example.com", email="alice@example.com")
    bob = UserFactory.create(username="bob@example.com", email="bob@example.com")
    Annotation.objects.create(
        item=item, team=queue.team, reviewer=alice, data={"quality_score": 5}, status=AnnotationStatus.SUBMITTED
    )
    Annotation.objects.create(
        item=item, team=queue.team, reviewer=bob, data={"quality_score": 2}, status=AnnotationStatus.SUBMITTED
    )

    _, rows = _pivot(queue)

    assert rows[0]["authoritative_annotator"] == ""


@pytest.mark.django_db()
def test_pivot_annotated_at_is_max_created_at_across_item_annotations():
    queue = AnnotationQueueFactory.create(
        schema={"quality_score": {"type": "int", "description": "Quality"}}, num_reviews_required=2
    )
    item = AnnotationItemFactory.create(queue=queue)
    alice = UserFactory.create(username="alice@example.com", email="alice@example.com")
    bob = UserFactory.create(username="bob@example.com", email="bob@example.com")
    earlier = Annotation.objects.create(
        item=item, team=queue.team, reviewer=alice, data={"quality_score": 5}, status=AnnotationStatus.SUBMITTED
    )
    Annotation.objects.filter(pk=earlier.pk).update(created_at=datetime(2024, 1, 1, tzinfo=UTC))
    later = Annotation.objects.create(
        item=item, team=queue.team, reviewer=bob, data={"quality_score": 2}, status=AnnotationStatus.SUBMITTED
    )
    Annotation.objects.filter(pk=later.pk).update(created_at=datetime(2024, 6, 1, tzinfo=UTC))

    _, rows = _pivot(queue)

    assert rows[0]["annotated_at"] == datetime(2024, 6, 1, tzinfo=UTC).isoformat()


@pytest.mark.django_db()
def test_pivot_flagged_item_with_no_annotations_produces_one_blank_row():
    queue = AnnotationQueueFactory.create(schema={"quality_score": {"type": "int", "description": "Quality"}})
    normal_item = AnnotationItemFactory.create(queue=queue)
    alice = UserFactory.create(username="alice@example.com", email="alice@example.com")
    Annotation.objects.create(
        item=normal_item,
        team=queue.team,
        reviewer=alice,
        data={"quality_score": 5},
        status=AnnotationStatus.SUBMITTED,
    )
    flag_reason = "Needs review"
    flagged_item = AnnotationItemFactory.create(
        queue=queue,
        status=AnnotationItemStatus.FLAGGED,
        flags=[{"user": "alice", "user_id": alice.id, "reason": flag_reason, "timestamp": "2024-01-01T00:00:00"}],
    )

    _, rows = _pivot(queue)

    flagged_rows = [r for r in rows if r["item_id"] == flagged_item.pk]
    assert len(flagged_rows) == 1
    row = flagged_rows[0]
    assert row["flagged"] is True
    assert row["field"] == ""
    assert row["authoritative_annotator"] == ""
    assert row["annotated_at"] == ""
    assert row["alice@example.com"] == ""
    assert flag_reason in row["flags"]


@pytest.mark.django_db()
def test_pivot_flagged_item_with_submitted_annotation_produces_single_flagged_row():
    """A flagged item can already have a submitted annotation (flagging doesn't block prior
    submissions). It must not also produce a separate, contradictory blank flagged=True row."""
    queue = AnnotationQueueFactory.create(schema={"quality_score": {"type": "int", "description": "Quality"}})
    alice = UserFactory.create(username="alice@example.com", email="alice@example.com")
    flag_reason = "Needs review"
    item = AnnotationItemFactory.create(
        queue=queue,
        status=AnnotationItemStatus.FLAGGED,
        flags=[{"user": "alice", "user_id": alice.id, "reason": flag_reason, "timestamp": "2024-01-01T00:00:00"}],
    )
    Annotation.objects.create(
        item=item, team=queue.team, reviewer=alice, data={"quality_score": 5}, status=AnnotationStatus.SUBMITTED
    )

    _, rows = _pivot(queue)

    item_rows = [r for r in rows if r["item_id"] == item.pk]
    assert len(item_rows) == 1
    row = item_rows[0]
    assert row["flagged"] is True
    assert row["field"] == "quality_score"
    assert row["alice@example.com"] == 5


@pytest.mark.django_db()
def test_pivot_pending_item_with_no_annotations_produces_no_rows():
    queue = AnnotationQueueFactory.create(schema={"quality_score": {"type": "int", "description": "Quality"}})
    annotated_item = AnnotationItemFactory.create(queue=queue)
    alice = UserFactory.create(username="alice@example.com", email="alice@example.com")
    Annotation.objects.create(
        item=annotated_item,
        team=queue.team,
        reviewer=alice,
        data={"quality_score": 5},
        status=AnnotationStatus.SUBMITTED,
    )
    pending_item = AnnotationItemFactory.create(queue=queue)  # no annotation, not flagged

    _, rows = _pivot(queue)

    assert len(rows) == 1  # only the annotated item's single schema field
    assert all(r["item_id"] != pending_item.pk for r in rows)


@pytest.mark.django_db()
def test_pivot_annotator_columns_sorted_alphabetically_regardless_of_submission_order():
    queue = AnnotationQueueFactory.create(schema={"quality_score": {"type": "int", "description": "Quality"}})
    item = AnnotationItemFactory.create(queue=queue)
    zack = UserFactory.create(username="zack@example.com", email="zack@example.com")
    alice = UserFactory.create(username="alice@example.com", email="alice@example.com")
    mike = UserFactory.create(username="mike@example.com", email="mike@example.com")
    # Submitted in reverse-alphabetical order on purpose.
    Annotation.objects.create(
        item=item, team=queue.team, reviewer=zack, data={"quality_score": 1}, status=AnnotationStatus.SUBMITTED
    )

    other_item = AnnotationItemFactory.create(queue=queue)
    Annotation.objects.create(
        item=other_item, team=queue.team, reviewer=mike, data={"quality_score": 2}, status=AnnotationStatus.SUBMITTED
    )
    Annotation.objects.create(
        item=other_item, team=queue.team, reviewer=alice, data={"quality_score": 3}, status=AnnotationStatus.SUBMITTED
    )

    fieldnames, _ = _pivot(queue)

    annotator_columns = fieldnames[8:]  # everything after the 8 fixed columns
    assert annotator_columns == ["alice@example.com", "mike@example.com", "zack@example.com"]


@pytest.mark.django_db()
def test_export_csv(client, team_with_users, queue, user):
    item = AnnotationItemFactory.create(queue=queue, team=team_with_users)
    Annotation.objects.create(
        item=item,
        team=team_with_users,
        reviewer=user,
        data={"quality_score": 5, "notes": "Great"},
        status=AnnotationStatus.SUBMITTED,
    )
    flag_reason = "Incorrect annotation"
    flagged_item = AnnotationItemFactory.create(
        queue=queue,
        team=team_with_users,
        status=AnnotationItemStatus.FLAGGED,
        flags=[{"user": user.username, "user_id": user.id, "reason": flag_reason, "timestamp": "2024-01-01T00:00:00"}],
    )
    # Flagged items have no annotations — flagging skips submission
    url = reverse("human_annotations:queue_export", args=[team_with_users.slug, queue.pk])
    response = client.get(url, {"format": "csv"})
    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv"
    reader = csv.DictReader(io.StringIO(response.content.decode()))
    rows = list(reader)

    # Normal item: one row per schema field, value under the reviewer's email column
    normal_rows = {r["field"]: r for r in rows if r["item_id"] == str(item.pk)}
    assert normal_rows.keys() == {"quality_score", "notes"}
    assert normal_rows["quality_score"][user.email] == "5"
    assert normal_rows["notes"][user.email] == "Great"
    assert normal_rows["quality_score"]["session_id"] == str(item.session.external_id)
    assert normal_rows["quality_score"]["flagged"] == "False"

    # Flagged item: exactly one row, no field, no annotator value
    flagged_rows = [r for r in rows if r["item_id"] == str(flagged_item.pk)]
    assert len(flagged_rows) == 1
    flagged = flagged_rows[0]
    assert flagged["session_id"] == str(flagged_item.session.external_id)
    assert flagged["flagged"] == "True"
    assert flag_reason in flagged["flags"]
    assert flagged["field"] == ""
    assert flagged[user.email] == ""


@pytest.mark.parametrize(
    ("raw_value", "expected_cell"),
    [
        pytest.param("=1+1", "'=1+1", id="equals-prefix"),
        pytest.param("+1+1", "'+1+1", id="plus-prefix"),
        pytest.param("-1+1", "'-1+1", id="minus-prefix"),
        pytest.param("@SUM(A1)", "'@SUM(A1)", id="at-prefix"),
        pytest.param("Great", "Great", id="ordinary-value-untouched"),
    ],
)
@pytest.mark.django_db()
def test_export_csv_neutralizes_formula_injection_in_annotation_values(
    client, team_with_users, queue, user, raw_value, expected_cell
):
    """Annotation values are annotator-controlled free text. A leading =, +, -, or @ would
    execute as a formula if the CSV is opened in spreadsheet software, so it must be neutralized."""
    item = AnnotationItemFactory.create(queue=queue, team=team_with_users)
    Annotation.objects.create(
        item=item,
        team=team_with_users,
        reviewer=user,
        data={"quality_score": 5, "notes": raw_value},
        status=AnnotationStatus.SUBMITTED,
    )
    url = reverse("human_annotations:queue_export", args=[team_with_users.slug, queue.pk])
    response = client.get(url, {"format": "csv"})
    reader = csv.DictReader(io.StringIO(response.content.decode()))
    rows = list(reader)

    notes_row = next(r for r in rows if r["field"] == "notes")
    assert notes_row[user.email] == expected_cell


@pytest.mark.django_db()
def test_export_csv_query_count_does_not_scale_with_annotation_count(
    client, team_with_users, queue, user, django_assert_max_num_queries
):
    other_user = UserFactory.create(username="other@example.com", email="other@example.com")
    for _ in range(3):
        item = AnnotationItemFactory.create(queue=queue, team=team_with_users)
        Annotation.objects.create(
            item=item, team=team_with_users, reviewer=user, data={"quality_score": 5}, status=AnnotationStatus.SUBMITTED
        )
        Annotation.objects.create(
            item=item,
            team=team_with_users,
            reviewer=other_user,
            data={"quality_score": 3},
            status=AnnotationStatus.SUBMITTED,
        )
    # 6 annotations from 2 distinct reviewers. Without select_related("reviewer") on the
    # annotations queryset, accessing ann.reviewer.email in _pivot_annotations issues one
    # query per annotation instead of per distinct reviewer - this bounds it regardless of
    # how many annotations exist. Baseline with the fix in place is 12 (session/auth/permission
    # overhead + queue/annotations/flagged_items lookups); ceiling leaves headroom for
    # unrelated incidental query changes without masking a real N+1 regression.
    url = reverse("human_annotations:queue_export", args=[team_with_users.slug, queue.pk])
    with django_assert_max_num_queries(15):
        response = client.get(url, {"format": "csv"})
    assert response.status_code == 200


@pytest.mark.django_db()
def test_export_jsonl(client, team_with_users, queue, user):
    item = AnnotationItemFactory.create(queue=queue, team=team_with_users)
    Annotation.objects.create(
        item=item,
        team=team_with_users,
        reviewer=user,
        data={"quality_score": 5, "notes": "Great"},
        status=AnnotationStatus.SUBMITTED,
    )
    flag_reason = "Spam content"
    flagged_item = AnnotationItemFactory.create(
        queue=queue,
        team=team_with_users,
        status=AnnotationItemStatus.FLAGGED,
        flags=[{"user": user.username, "user_id": user.id, "reason": flag_reason, "timestamp": "2024-01-01T00:00:00"}],
    )
    # Flagged items have no annotations — flagging skips submission
    experiment_session = ExperimentSessionFactory.create(team=team_with_users)
    chat_message = ChatMessageFactory.create(chat=experiment_session.chat, message_type="human", content="test")
    message_item = AnnotationItemFactory.create(
        queue=queue,
        team=team_with_users,
        session=None,
        item_type="message",
        message=chat_message,
    )
    Annotation.objects.create(
        item=message_item,
        team=team_with_users,
        reviewer=user,
        data={"quality_score": 4, "notes": ""},
        status=AnnotationStatus.SUBMITTED,
    )
    url = reverse("human_annotations:queue_export", args=[team_with_users.slug, queue.pk])
    response = client.get(url, {"format": "jsonl"})
    assert response.status_code == 200
    assert response["Content-Type"] == "application/jsonl"
    records = [json.loads(line) for line in response.content.decode().strip().split("\n")]
    records_by_item = {r["item_id"]: r for r in records}

    # Normal item — has submitted annotation
    normal = records_by_item[item.pk]
    assert normal["annotation"]["quality_score"] == 5
    assert normal["session_id"] == str(item.session.external_id)
    assert normal["flagged"] is False
    assert normal["flags"] == []

    # Flagged item — no annotation, but still appears in export
    flagged = records_by_item[flagged_item.pk]
    assert flagged["flagged"] is True
    assert flagged["flags"] == [
        {"user": user.username, "user_id": user.id, "reason": flag_reason, "timestamp": "2024-01-01T00:00:00"}
    ]
    assert flagged["annotated_at"] == ""
    assert flagged["annotation"] == {}

    # Message item — session_id falls back to message.chat.experiment_session
    msg = records_by_item[message_item.pk]
    assert msg["session_id"] == str(experiment_session.external_id)


# ===== Multi-Review =====


@pytest.mark.django_db()
def test_multi_review_second_user_can_annotate(team_with_users):
    """After user1 annotates all items, user2 should still see them when num_reviews_required > 1."""
    user1 = team_with_users.members.first()
    user2 = team_with_users.members.last()
    assert user1 != user2

    queue = AnnotationQueueFactory.create(team=team_with_users, created_by=user1, num_reviews_required=2)
    item = AnnotationItemFactory.create(queue=queue, team=team_with_users)

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
def test_multi_review_item_awaits_resolution_after_enough_reviews(team_with_users):
    """Multi-reviewer items move to AWAITING_RESOLUTION once enough reviews are in;
    COMPLETED requires an authoritative annotation to be picked."""
    user1 = team_with_users.members.first()
    user2 = team_with_users.members.last()

    queue = AnnotationQueueFactory.create(team=team_with_users, created_by=user1, num_reviews_required=2)
    item = AnnotationItemFactory.create(queue=queue, team=team_with_users)

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

    # Second review - reaches required count without authoritative pick
    Annotation.objects.create(
        item=item,
        team=team_with_users,
        reviewer=user2,
        data={"quality_score": 5, "notes": "Great"},
        status=AnnotationStatus.SUBMITTED,
    )
    item.refresh_from_db()
    assert item.review_count == 2
    assert item.status == AnnotationItemStatus.AWAITING_RESOLUTION


@pytest.mark.django_db()
def test_progress_accounts_for_multiple_reviews(team_with_users):
    """Progress should reflect review-level progress, not just item completion."""
    user1 = team_with_users.members.first()

    queue = AnnotationQueueFactory.create(team=team_with_users, created_by=user1, num_reviews_required=2)
    AnnotationItemFactory.create(queue=queue, team=team_with_users)
    item2 = AnnotationItemFactory.create(queue=queue, team=team_with_users)

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


# ===== Add Session to Queue from Session Detail =====


@pytest.fixture()
def session(team_with_users):
    return ExperimentSessionFactory.create(
        team=team_with_users, chat__team=team_with_users, experiment__team=team_with_users
    )


@pytest.fixture()
def human_annotations_flag():
    flag, _ = Flag.objects.get_or_create(name="flag_human_annotations")
    flag.everyone = True
    flag.save()
    flag.flush()  # Clear waffle cache immediately; save() uses on_commit which doesn't run in tests
    return flag


@pytest.mark.django_db()
def test_add_session_to_queue_get_lists_active_queues(client, team_with_users, queue, session, human_annotations_flag):
    """GET returns the modal partial listing active queues for the team."""
    url = reverse("human_annotations:session_add_to_queue", args=[team_with_users.slug, session.external_id])
    response = client.get(url)
    assert response.status_code == 200
    assert queue.name in response.content.decode()


@pytest.mark.django_db()
def test_add_session_to_queue_get_shows_already_added(client, team_with_users, queue, session, human_annotations_flag):
    """If the session is already in a queue, that queue shows an 'Already added' badge and its radio is disabled."""
    AnnotationItem.objects.create(
        queue=queue,
        session=session,
        team=team_with_users,
        item_type="session",
    )
    url = reverse("human_annotations:session_add_to_queue", args=[team_with_users.slug, session.external_id])
    response = client.get(url)
    assert response.status_code == 200
    content = response.content.decode()
    assert queue.name in content
    assert "Already added" in content
    assert "disabled" in content


@pytest.mark.django_db()
def test_add_session_to_queue_get_excludes_inactive_queues(client, team_with_users, session, human_annotations_flag):
    """GET only lists ACTIVE queues — paused/archived queues must not appear."""
    paused_queue = AnnotationQueueFactory.create(team=team_with_users, status="paused")
    url = reverse("human_annotations:session_add_to_queue", args=[team_with_users.slug, session.external_id])
    response = client.get(url)
    assert response.status_code == 200
    assert paused_queue.name not in response.content.decode()


@pytest.mark.django_db()
def test_add_session_to_queue_post_creates_item(client, team_with_users, queue, session, human_annotations_flag):
    """POST with a valid queue_id creates an AnnotationItem and returns 200 with queue name."""
    url = reverse("human_annotations:session_add_to_queue", args=[team_with_users.slug, session.external_id])
    response = client.post(url, {"queue_id": queue.pk})
    assert response.status_code == 200
    assert AnnotationItem.objects.filter(queue=queue, session=session).exists()
    assert queue.name in response.content.decode()


@pytest.mark.django_db()
def test_add_session_to_queue_post_duplicate_returns_200(
    client, team_with_users, queue, session, human_annotations_flag
):
    """POST with a session already in the queue returns 200 with an 'already' message (no duplicate row)."""
    AnnotationItem.objects.create(
        queue=queue,
        session=session,
        team=team_with_users,
        item_type="session",
    )
    url = reverse("human_annotations:session_add_to_queue", args=[team_with_users.slug, session.external_id])
    response = client.post(url, {"queue_id": queue.pk})
    assert response.status_code == 200
    assert AnnotationItem.objects.filter(queue=queue, session=session).count() == 1
    assert "already" in response.content.decode().lower()


# ===== AnnotationSessionsSelectionTable =====


@pytest.mark.django_db()
def test_annotation_sessions_selection_table_has_selection_column(team_with_users):
    session = ExperimentSessionFactory.create(team=team_with_users)
    table = AnnotationSessionsSelectionTable([session])
    assert "selection" in table.columns
    assert "experiment" in table.columns
    assert "participant" in table.columns
    assert "message_count" in table.columns


# ===== Queue sessions table & JSON views =====


@pytest.mark.django_db()
def test_queue_sessions_table_view(client, team_with_users, queue):
    ExperimentSessionFactory.create_batch(3, team=team_with_users)
    url = reverse("human_annotations:queue_sessions_table", args=[team_with_users.slug, queue.pk])
    response = client.get(url)
    assert response.status_code == 200


@pytest.mark.django_db()
def test_queue_sessions_table_only_shows_team_sessions(client, team_with_users, queue):
    own_session = ExperimentSessionFactory.create(team=team_with_users)
    ChatMessageFactory.create(chat=own_session.chat)
    other_session = ExperimentSessionFactory.create()  # different team
    ChatMessageFactory.create(chat=other_session.chat)
    url = reverse("human_annotations:queue_sessions_table", args=[team_with_users.slug, queue.pk])
    response = client.get(url)
    content = response.content.decode()
    assert str(own_session.external_id) in content
    assert str(other_session.external_id) not in content


@pytest.mark.django_db()
def test_queue_sessions_count_returns_total(client, team_with_users, queue):
    sessions = ExperimentSessionFactory.create_batch(3, team=team_with_users)
    for s in sessions:
        ChatMessageFactory.create(chat=s.chat)
    ExperimentSessionFactory.create()  # different team — must not appear
    url = reverse("human_annotations:queue_sessions_count", args=[team_with_users.slug, queue.pk])
    response = client.get(url)
    assert response.status_code == 200
    data = response.json()
    assert "ids" not in data
    assert data["total"] == 3


@pytest.mark.django_db()
def test_queue_sessions_count_requires_login(team_with_users, queue):
    c = Client()  # unauthenticated
    url = reverse("human_annotations:queue_sessions_count", args=[team_with_users.slug, queue.pk])
    response = c.get(url)
    assert response.status_code in (302, 403)


@pytest.mark.django_db()
def test_queue_sessions_table_excludes_sessions_without_messages(client, team_with_users, queue):
    session_with_messages = ExperimentSessionFactory.create(team=team_with_users)
    ChatMessageFactory.create(chat=session_with_messages.chat)
    session_without_messages = ExperimentSessionFactory.create(team=team_with_users)

    url = reverse("human_annotations:queue_sessions_table", args=[team_with_users.slug, queue.pk])
    response = client.get(url)
    content = response.content.decode()
    assert str(session_with_messages.external_id) in content
    assert str(session_without_messages.external_id) not in content


@pytest.mark.django_db()
def test_queue_sessions_count_excludes_sessions_without_messages(client, team_with_users, queue):
    session_with_messages = ExperimentSessionFactory.create(team=team_with_users)
    ChatMessageFactory.create(chat=session_with_messages.chat)
    ExperimentSessionFactory.create(team=team_with_users)  # no messages

    url = reverse("human_annotations:queue_sessions_count", args=[team_with_users.slug, queue.pk])
    data = client.get(url).json()
    assert data["total"] == 1
    assert "ids" not in data


@pytest.mark.django_db()
def test_queue_sessions_table_excludes_evaluation_sessions(client, team_with_users, queue):
    normal_session = ExperimentSessionFactory.create(team=team_with_users)
    ChatMessageFactory.create(chat=normal_session.chat)
    eval_session = ExperimentSessionFactory.create(team=team_with_users, platform=ChannelPlatform.EVALUATIONS)
    ChatMessageFactory.create(chat=eval_session.chat)

    url = reverse("human_annotations:queue_sessions_table", args=[team_with_users.slug, queue.pk])
    response = client.get(url)
    content = response.content.decode()
    assert str(normal_session.external_id) in content
    assert str(eval_session.external_id) not in content


@pytest.mark.django_db()
def test_queue_sessions_count_excludes_evaluation_sessions(client, team_with_users, queue):
    normal_session = ExperimentSessionFactory.create(team=team_with_users)
    ChatMessageFactory.create(chat=normal_session.chat)
    eval_session = ExperimentSessionFactory.create(team=team_with_users, platform=ChannelPlatform.EVALUATIONS)
    ChatMessageFactory.create(chat=eval_session.chat)

    url = reverse("human_annotations:queue_sessions_count", args=[team_with_users.slug, queue.pk])
    data = client.get(url).json()
    assert data["total"] == 1
    assert "ids" not in data


# ===== AddSessionsToQueue GET + POST =====


@pytest.mark.django_db()
def test_add_sessions_get_renders_filter_context(client, team_with_users, queue):
    url = reverse("human_annotations:queue_add_sessions", args=[team_with_users.slug, queue.pk])
    response = client.get(url)
    assert response.status_code == 200
    assert "df_filter_columns" in response.context
    assert "df_filter_data_source_url" in response.context
    assert "sessions_count_url" in response.context


@pytest.mark.django_db()
def test_add_sessions_post_creates_items_from_external_ids(client, team_with_users, queue):
    sessions = ExperimentSessionFactory.create_batch(2, team=team_with_users)
    session_ids = ",".join(str(s.external_id) for s in sessions)
    url = reverse("human_annotations:queue_add_sessions", args=[team_with_users.slug, queue.pk])
    response = client.post(url, {"session_ids": session_ids})
    assert response.status_code == 302
    assert response["Location"] == reverse("human_annotations:queue_detail", args=[team_with_users.slug, queue.pk])
    assert AnnotationItem.objects.filter(queue=queue).count() == 2


@pytest.mark.django_db()
def test_add_sessions_post_skips_duplicates(client, team_with_users, queue):
    item = AnnotationItemFactory.create(queue=queue, team=team_with_users)
    existing_session = item.session
    new_session = ExperimentSessionFactory.create(team=team_with_users)
    session_ids = ",".join([str(existing_session.external_id), str(new_session.external_id)])
    url = reverse("human_annotations:queue_add_sessions", args=[team_with_users.slug, queue.pk])
    client.post(url, {"session_ids": session_ids})
    assert AnnotationItem.objects.filter(queue=queue).count() == 2  # 1 old + 1 new


@pytest.mark.django_db()
def test_add_sessions_post_empty_redirects_with_error(client, team_with_users, queue):
    url = reverse("human_annotations:queue_add_sessions", args=[team_with_users.slug, queue.pk])
    response = client.post(url, {"session_ids": ""})
    assert response.status_code == 302
    assert AnnotationItem.objects.filter(queue=queue).count() == 0


@pytest.mark.django_db()
def test_add_sessions_post_ignores_other_team_sessions(client, team_with_users, queue):
    other_session = ExperimentSessionFactory.create()  # different team
    url = reverse("human_annotations:queue_add_sessions", args=[team_with_users.slug, queue.pk])
    client.post(url, {"session_ids": str(other_session.external_id)})
    assert AnnotationItem.objects.filter(queue=queue).count() == 0


# ===== AddSessionsToQueue: all_matching mode =====


@pytest.mark.django_db()
def test_add_sessions_all_matching_adds_all_sessions_with_messages(client, team_with_users, queue):
    sessions = ExperimentSessionFactory.create_batch(3, team=team_with_users)
    for s in sessions:
        ChatMessageFactory.create(chat=s.chat)
    # Session without messages should be excluded
    ExperimentSessionFactory.create(team=team_with_users)

    url = reverse("human_annotations:queue_add_sessions", args=[team_with_users.slug, queue.pk])
    response = client.post(url, {"mode": "all_matching"})
    assert response.status_code == 302
    assert AnnotationItem.objects.filter(queue=queue).count() == 3


@pytest.mark.django_db()
def test_add_sessions_all_matching_skips_already_queued(client, team_with_users, queue):
    sessions = ExperimentSessionFactory.create_batch(3, team=team_with_users)
    for s in sessions:
        ChatMessageFactory.create(chat=s.chat)
    # Pre-add one session to the queue
    AnnotationItemFactory.create(queue=queue, team=team_with_users, session=sessions[0])

    url = reverse("human_annotations:queue_add_sessions", args=[team_with_users.slug, queue.pk])
    response = client.post(url, {"mode": "all_matching"})
    assert response.status_code == 302
    # 2 new + 1 existing = 3 total items, but only 2 newly created
    assert AnnotationItem.objects.filter(queue=queue).count() == 3


@pytest.mark.django_db()
def test_add_sessions_all_matching_empty_results(client, team_with_users, queue):
    url = reverse("human_annotations:queue_add_sessions", args=[team_with_users.slug, queue.pk])
    response = client.post(url, {"mode": "all_matching"})
    assert response.status_code == 302
    assert AnnotationItem.objects.filter(queue=queue).count() == 0


# ===== AddSessionsToQueue: sample mode =====


@pytest.mark.django_db()
def test_add_sessions_sample_adds_subset(client, team_with_users, queue):
    sessions = ExperimentSessionFactory.create_batch(10, team=team_with_users)
    for s in sessions:
        ChatMessageFactory.create(chat=s.chat)

    url = reverse("human_annotations:queue_add_sessions", args=[team_with_users.slug, queue.pk])
    response = client.post(url, {"mode": "sample", "sample_percent": "50"})
    assert response.status_code == 302
    count = AnnotationItem.objects.filter(queue=queue).count()
    assert 1 <= count <= 10  # Should be ~5 but random


@pytest.mark.django_db()
def test_add_sessions_sample_100_percent_adds_all(client, team_with_users, queue):
    sessions = ExperimentSessionFactory.create_batch(5, team=team_with_users)
    for s in sessions:
        ChatMessageFactory.create(chat=s.chat)

    url = reverse("human_annotations:queue_add_sessions", args=[team_with_users.slug, queue.pk])
    response = client.post(url, {"mode": "sample", "sample_percent": "100"})
    assert response.status_code == 302
    assert AnnotationItem.objects.filter(queue=queue).count() == 5


@pytest.mark.django_db()
def test_add_sessions_sample_clamps_invalid_percent(client, team_with_users, queue):
    sessions = ExperimentSessionFactory.create_batch(5, team=team_with_users)
    for s in sessions:
        ChatMessageFactory.create(chat=s.chat)

    url = reverse("human_annotations:queue_add_sessions", args=[team_with_users.slug, queue.pk])
    # Percent > 100 should be clamped to 100
    response = client.post(url, {"mode": "sample", "sample_percent": "200"})
    assert response.status_code == 302
    assert AnnotationItem.objects.filter(queue=queue).count() == 5


@pytest.mark.django_db()
def test_add_sessions_sample_empty_results(client, team_with_users, queue):
    url = reverse("human_annotations:queue_add_sessions", args=[team_with_users.slug, queue.pk])
    response = client.post(url, {"mode": "sample", "sample_percent": "50"})
    assert response.status_code == 302
    assert AnnotationItem.objects.filter(queue=queue).count() == 0


# ===== Sessions JSON endpoint =====


@pytest.mark.django_db()
def test_queue_sessions_count_returns_only_total(client, team_with_users, queue):
    sessions = ExperimentSessionFactory.create_batch(3, team=team_with_users)
    for s in sessions:
        ChatMessageFactory.create(chat=s.chat)

    url = reverse("human_annotations:queue_sessions_count", args=[team_with_users.slug, queue.pk])
    response = client.get(url)
    assert response.status_code == 200
    data = response.json()
    assert "ids" not in data
    assert "total" in data
    assert data["total"] == 3


# ===== Annotation Reviewer Role =====


@pytest.fixture()
def reviewer_membership(team_with_users):
    """A team membership with only the Annotation Reviewer role."""

    reviewer_group = Group.objects.get(name=ANNOTATION_REVIEWER_GROUP)
    return MembershipFactory.create(team=team_with_users, groups=[reviewer_group])


@pytest.fixture()
def reviewer_client(reviewer_membership):
    c = Client()
    c.force_login(reviewer_membership.user)
    return c


@pytest.mark.django_db()
def test_reviewer_can_view_queue_home(reviewer_client, team_with_users):
    url = reverse("human_annotations:queue_home", args=[team_with_users.slug])
    response = reviewer_client.get(url)
    assert response.status_code == 200
    # No "new queue" button for reviewers
    assert "new_object_url" not in response.context


@pytest.mark.django_db()
def test_reviewer_queue_table_only_shows_assigned_queues(reviewer_client, reviewer_membership, team_with_users, user):
    assigned_queue = AnnotationQueueFactory.create(team=team_with_users, created_by=user)
    assigned_queue.assignees.add(reviewer_membership.user)
    unassigned_queue = AnnotationQueueFactory.create(team=team_with_users, created_by=user)
    unassigned_queue.assignees.add(user)  # assigned to someone else

    url = reverse("human_annotations:queue_table", args=[team_with_users.slug])
    response = reviewer_client.get(url)
    assert response.status_code == 200
    queues = response.context["object_list"]
    assert queues.filter(pk=assigned_queue.pk).exists()
    assert not queues.filter(pk=unassigned_queue.pk).exists()


@pytest.mark.django_db()
def test_reviewer_queue_table_hides_unassigned_queues(reviewer_client, team_with_users, user):
    """Queues with no assignees are NOT visible to reviewers — only directly assigned queues are."""
    open_queue = AnnotationQueueFactory.create(team=team_with_users, created_by=user)
    assert not open_queue.assignees.exists()

    url = reverse("human_annotations:queue_table", args=[team_with_users.slug])
    response = reviewer_client.get(url)
    assert response.status_code == 200
    assert not response.context["object_list"].filter(pk=open_queue.pk).exists()


@pytest.mark.django_db()
def test_reviewer_can_annotate_assigned_queue(reviewer_client, reviewer_membership, team_with_users, user, queue):
    queue.assignees.add(reviewer_membership.user)
    AnnotationItemFactory.create(queue=queue, team=team_with_users)
    url = reverse("human_annotations:annotate_queue", args=[team_with_users.slug, queue.pk])
    response = reviewer_client.get(url)
    assert response.status_code == 200
    assert response.context["can_annotate"] is True


@pytest.mark.django_db()
def test_reviewer_cannot_annotate_unassigned_queue(reviewer_client, team_with_users, user, queue):
    """If queue has assignees and reviewer is not one of them, they are redirected."""
    queue.assignees.add(user)  # reviewer is not in assignees
    AnnotationItemFactory.create(queue=queue, team=team_with_users)
    url = reverse("human_annotations:annotate_queue", args=[team_with_users.slug, queue.pk])
    response = reviewer_client.get(url)
    assert response.status_code == 302


@pytest.mark.django_db()
def test_reviewer_cannot_create_queue(reviewer_client, team_with_users):
    url = reverse("human_annotations:queue_new", args=[team_with_users.slug])
    response = reviewer_client.get(url)
    assert response.status_code == 403


@pytest.mark.django_db()
def test_reviewer_cannot_add_sessions_to_queue(reviewer_client, team_with_users, queue):
    url = reverse("human_annotations:queue_add_sessions", args=[team_with_users.slug, queue.pk])
    response = reviewer_client.get(url)
    assert response.status_code == 403


@pytest.mark.django_db()
def test_reviewer_cannot_manage_assignees(reviewer_client, team_with_users, queue):
    url = reverse("human_annotations:queue_manage_assignees", args=[team_with_users.slug, queue.pk])
    response = reviewer_client.get(url)
    assert response.status_code == 403


@pytest.mark.django_db()
def test_reviewer_cannot_export_annotations(reviewer_client, team_with_users, queue):
    url = reverse("human_annotations:queue_export", args=[team_with_users.slug, queue.pk])
    response = reviewer_client.get(url)
    assert response.status_code == 403


@pytest.mark.django_db()
def test_reviewer_detail_url_returns_404_for_unassigned_queue(reviewer_client, team_with_users, user):
    """Reviewer cannot access queue detail directly by PK if not assigned."""
    unassigned_queue = AnnotationQueueFactory.create(team=team_with_users, created_by=user)
    url = reverse("human_annotations:queue_detail", args=[team_with_users.slug, unassigned_queue.pk])
    response = reviewer_client.get(url)
    assert response.status_code == 404


@pytest.mark.django_db()
def test_reviewer_detail_url_returns_200_for_assigned_queue(
    reviewer_client, reviewer_membership, team_with_users, user
):
    """Reviewer can access queue detail directly if assigned."""
    assigned_queue = AnnotationQueueFactory.create(team=team_with_users, created_by=user)
    assigned_queue.assignees.add(reviewer_membership.user)
    url = reverse("human_annotations:queue_detail", args=[team_with_users.slug, assigned_queue.pk])
    response = reviewer_client.get(url)
    assert response.status_code == 200


@pytest.mark.django_db()
def test_reviewer_items_table_returns_empty_for_unassigned_queue(reviewer_client, team_with_users, user):
    """Reviewer gets an empty items table (not 404) when accessing an unassigned queue's items by PK."""
    unassigned_queue = AnnotationQueueFactory.create(team=team_with_users, created_by=user)
    AnnotationItemFactory.create(queue=unassigned_queue, team=team_with_users)
    url = reverse("human_annotations:queue_items_table", args=[team_with_users.slug, unassigned_queue.pk])
    response = reviewer_client.get(url)
    assert response.status_code == 200
    assert unassigned_queue.name not in response.content.decode()


# ===== Remove Session from Queue =====


@pytest.mark.django_db()
def test_remove_session_get_shows_confirmation(client, team_with_users, queue, user):
    """GET returns the confirmation modal partial with annotation data."""
    item = AnnotationItemFactory.create(queue=queue, team=team_with_users)
    Annotation.objects.create(
        item=item,
        team=team_with_users,
        reviewer=user,
        data={"quality_score": 4, "notes": "Good"},
        status=AnnotationStatus.SUBMITTED,
    )
    url = reverse("human_annotations:queue_remove_item", args=[team_with_users.slug, queue.pk, item.pk])
    response = client.get(url)
    assert response.status_code == 200
    content = response.content.decode()
    assert "Remove Session from Queue" in content
    assert "quality_score" in content


@pytest.mark.django_db()
def test_remove_session_get_no_annotations(client, team_with_users, queue):
    """GET for an item with no annotations shows the empty state."""
    item = AnnotationItemFactory.create(queue=queue, team=team_with_users)
    url = reverse("human_annotations:queue_remove_item", args=[team_with_users.slug, queue.pk, item.pk])
    response = client.get(url)
    assert response.status_code == 200
    assert "No annotations" in response.content.decode()


@pytest.mark.django_db()
def test_remove_session_delete_removes_item(client, team_with_users, queue):
    """DELETE removes the annotation item from the queue."""
    item = AnnotationItemFactory.create(queue=queue, team=team_with_users)
    url = reverse("human_annotations:queue_remove_item", args=[team_with_users.slug, queue.pk, item.pk])
    response = client.delete(url)
    assert response.status_code == 200
    assert not AnnotationItem.objects.filter(pk=item.pk).exists()


@pytest.mark.django_db()
def test_remove_session_delete_cascades_annotations(client, team_with_users, queue, user):
    """DELETE also removes all associated annotations."""
    item = AnnotationItemFactory.create(queue=queue, team=team_with_users)
    annotation = Annotation.objects.create(
        item=item,
        team=team_with_users,
        reviewer=user,
        data={"quality_score": 4, "notes": "Good"},
        status=AnnotationStatus.SUBMITTED,
    )
    url = reverse("human_annotations:queue_remove_item", args=[team_with_users.slug, queue.pk, item.pk])
    client.delete(url)
    assert not Annotation.objects.filter(pk=annotation.pk).exists()


@pytest.mark.django_db()
def test_remove_session_wrong_queue_returns_404(client, team_with_users, queue, user):
    """DELETE with item from a different queue returns 404."""
    other_queue = AnnotationQueueFactory.create(team=team_with_users, created_by=user)
    item = AnnotationItemFactory.create(queue=other_queue, team=team_with_users)
    url = reverse("human_annotations:queue_remove_item", args=[team_with_users.slug, queue.pk, item.pk])
    response = client.delete(url)
    assert response.status_code == 404
    assert AnnotationItem.objects.filter(pk=item.pk).exists()


@pytest.mark.django_db()
def test_reviewer_cannot_remove_session(reviewer_client, team_with_users, queue):
    """Reviewers (without delete_annotationitem perm) cannot remove sessions."""
    item = AnnotationItemFactory.create(queue=queue, team=team_with_users)
    url = reverse("human_annotations:queue_remove_item", args=[team_with_users.slug, queue.pk, item.pk])
    response = reviewer_client.delete(url)
    assert response.status_code == 403
    assert AnnotationItem.objects.filter(pk=item.pk).exists()


# ===== Import from Dataset =====


@pytest.mark.django_db()
def test_import_from_dataset_get_renders_form(client, team_with_users, queue):
    url = reverse("human_annotations:queue_import_from_dataset", args=[team_with_users.slug, queue.pk])
    response = client.get(url)
    assert response.status_code == 200
    assert "form" in response.context
    assert "queue" in response.context


@pytest.mark.django_db()
def test_import_from_dataset_get_requires_permission(reviewer_client, reviewer_membership, team_with_users, queue):
    url = reverse("human_annotations:queue_import_from_dataset", args=[team_with_users.slug, queue.pk])
    response = reviewer_client.get(url)
    assert response.status_code == 403


@pytest.mark.django_db()
def test_import_from_dataset_post_creates_annotation_items(client, team_with_users, queue):
    session = ExperimentSessionFactory.create(team=team_with_users)
    msg = EvaluationMessageFactory.create(session=session)
    dataset = EvaluationDatasetFactory.create(team=team_with_users, messages=[msg])

    url = reverse("human_annotations:queue_import_from_dataset", args=[team_with_users.slug, queue.pk])
    response = client.post(url, {"dataset": dataset.pk})

    assert response.status_code == 302
    assert AnnotationItem.objects.filter(queue=queue, session=session).exists()


@pytest.mark.django_db()
def test_import_from_dataset_post_skips_existing_sessions(client, team_with_users, queue):
    session = ExperimentSessionFactory.create(team=team_with_users)
    msg = EvaluationMessageFactory.create(session=session)
    dataset = EvaluationDatasetFactory.create(team=team_with_users, messages=[msg])
    AnnotationItemFactory.create(queue=queue, team=team_with_users, session=session)

    url = reverse("human_annotations:queue_import_from_dataset", args=[team_with_users.slug, queue.pk])
    response = client.post(url, {"dataset": dataset.pk})

    assert response.status_code == 302
    assert AnnotationItem.objects.filter(queue=queue).count() == 1


@pytest.mark.django_db()
def test_import_from_dataset_post_no_sessions_redirects_with_error(client, team_with_users, queue):
    msg = EvaluationMessageFactory.create()  # no session FK
    dataset = EvaluationDatasetFactory.create(team=team_with_users, messages=[msg])

    url = reverse("human_annotations:queue_import_from_dataset", args=[team_with_users.slug, queue.pk])
    response = client.post(url, {"dataset": dataset.pk})

    assert response.status_code == 302
    assert AnnotationItem.objects.filter(queue=queue).count() == 0


@pytest.mark.django_db()
def test_import_from_dataset_post_invalid_form_rerenders(client, team_with_users, queue):
    url = reverse("human_annotations:queue_import_from_dataset", args=[team_with_users.slug, queue.pk])
    response = client.post(url, {"dataset": ""})
    assert response.status_code == 200
    assert "form" in response.context
    assert response.context["form"].errors


@pytest.mark.django_db()
def test_import_from_dataset_post_all_duplicates_creates_no_new_items(client, team_with_users, queue):
    session1 = ExperimentSessionFactory.create(team=team_with_users)
    session2 = ExperimentSessionFactory.create(team=team_with_users)
    msg1 = EvaluationMessageFactory.create(session=session1)
    msg2 = EvaluationMessageFactory.create(session=session2)
    dataset = EvaluationDatasetFactory.create(team=team_with_users, messages=[msg1, msg2])
    AnnotationItemFactory.create(queue=queue, team=team_with_users, session=session1)
    AnnotationItemFactory.create(queue=queue, team=team_with_users, session=session2)

    url = reverse("human_annotations:queue_import_from_dataset", args=[team_with_users.slug, queue.pk])
    response = client.post(url, {"dataset": dataset.pk})

    assert response.status_code == 302
    assert AnnotationItem.objects.filter(queue=queue).count() == 2


@pytest.mark.django_db()
def test_queue_detail_shows_awaiting_resolution_callout(client, team_with_users, user):
    user2 = User.objects.create(username="r2-detail", email="r2-detail@e.com")
    MembershipFactory.create(team=team_with_users, user=user2)
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
    assert b"1 awaiting resolution" in response.content
    # And confirm the renamed label is present too.
    assert b"items resolved" in response.content


@pytest.mark.django_db()
def test_export_csv_includes_authoritative_annotator(client, team_with_users, user):
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
    assert fieldnames is not None
    assert "authoritative_annotator" in fieldnames
    assert "is_authoritative" not in fieldnames
    rows = list(reader)
    assert rows[0]["authoritative_annotator"] == user.email


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
