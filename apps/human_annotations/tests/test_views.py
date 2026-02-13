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
    AnnotationSchema,
    AnnotationStatus,
)
from apps.utils.factories.human_annotations import (
    AnnotationItemFactory,
    AnnotationQueueFactory,
    AnnotationSchemaFactory,
)
from apps.utils.factories.team import TeamWithUsersFactory


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
def schema(team_with_users):
    return AnnotationSchemaFactory(team=team_with_users)


@pytest.fixture()
def queue(team_with_users, schema, user):
    return AnnotationQueueFactory(team=team_with_users, schema=schema, created_by=user)


# ===== Schema CRUD =====


@pytest.mark.django_db()
def test_schema_home(client, team_with_users):
    url = reverse("human_annotations:schema_home", args=[team_with_users.slug])
    response = client.get(url)
    assert response.status_code == 200


@pytest.mark.django_db()
def test_schema_table(client, team_with_users, schema):
    url = reverse("human_annotations:schema_table", args=[team_with_users.slug])
    response = client.get(url)
    assert response.status_code == 200
    assert schema.name in response.content.decode()


@pytest.mark.django_db()
def test_create_schema(client, team_with_users):
    url = reverse("human_annotations:schema_new", args=[team_with_users.slug])
    data = {
        "name": "Test Schema",
        "description": "A test",
        "schema": json.dumps({"score": {"type": "int", "description": "Score", "ge": 1, "le": 5}}),
    }
    response = client.post(url, data)
    assert response.status_code == 302
    assert AnnotationSchema.objects.filter(name="Test Schema", team=team_with_users).exists()


@pytest.mark.django_db()
def test_create_schema_invalid_json(client, team_with_users):
    url = reverse("human_annotations:schema_new", args=[team_with_users.slug])
    data = {
        "name": "Bad Schema",
        "schema": "not json",
    }
    response = client.post(url, data)
    assert response.status_code == 200  # re-renders form with errors
    assert not AnnotationSchema.objects.filter(name="Bad Schema").exists()


@pytest.mark.django_db()
def test_edit_schema(client, team_with_users, schema):
    url = reverse("human_annotations:schema_edit", args=[team_with_users.slug, schema.pk])
    data = {
        "name": "Updated Schema",
        "description": schema.description,
        "schema": json.dumps(schema.schema),
    }
    response = client.post(url, data)
    assert response.status_code == 302
    schema.refresh_from_db()
    assert schema.name == "Updated Schema"


@pytest.mark.django_db()
def test_delete_schema_no_queues(client, team_with_users):
    schema = AnnotationSchemaFactory(team=team_with_users)
    url = reverse("human_annotations:schema_delete", args=[team_with_users.slug, schema.pk])
    response = client.delete(url)
    assert response.status_code == 200
    assert not AnnotationSchema.objects.filter(pk=schema.pk).exists()


@pytest.mark.django_db()
def test_delete_schema_with_queues(client, team_with_users, schema, queue):
    url = reverse("human_annotations:schema_delete", args=[team_with_users.slug, schema.pk])
    response = client.delete(url)
    assert response.status_code == 400
    assert AnnotationSchema.objects.filter(pk=schema.pk).exists()


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
def test_create_queue(client, team_with_users, schema):
    url = reverse("human_annotations:queue_new", args=[team_with_users.slug])
    data = {
        "name": "New Queue",
        "description": "A test queue",
        "schema": schema.pk,
        "num_reviews_required": 2,
    }
    response = client.post(url, data)
    assert response.status_code == 302
    assert AnnotationQueue.objects.filter(name="New Queue", team=team_with_users).exists()


@pytest.mark.django_db()
def test_queue_detail(client, team_with_users, queue):
    url = reverse("human_annotations:queue_detail", args=[team_with_users.slug, queue.pk])
    response = client.get(url)
    assert response.status_code == 200
    assert queue.name in response.content.decode()


@pytest.mark.django_db()
def test_queue_items_table(client, team_with_users, queue):
    AnnotationItemFactory(queue=queue, team=team_with_users)
    url = reverse("human_annotations:queue_items_table", args=[team_with_users.slug, queue.pk])
    response = client.get(url)
    assert response.status_code == 200


# ===== Assignee Management =====


@pytest.mark.django_db()
def test_manage_assignees_get(client, team_with_users, queue):
    url = reverse("human_annotations:queue_manage_assignees", args=[team_with_users.slug, queue.pk])
    response = client.get(url)
    assert response.status_code == 200


@pytest.mark.django_db()
def test_manage_assignees_post(client, team_with_users, queue, user):
    url = reverse("human_annotations:queue_manage_assignees", args=[team_with_users.slug, queue.pk])
    response = client.post(url, {"assignees": [user.pk]})
    assert response.status_code == 302
    assert user in queue.assignees.all()


# ===== CSV Import =====


@pytest.mark.django_db()
def test_import_csv_get(client, team_with_users, queue):
    url = reverse("human_annotations:queue_import_csv", args=[team_with_users.slug, queue.pk])
    response = client.get(url)
    assert response.status_code == 200


@pytest.mark.django_db()
def test_import_csv_post(client, team_with_users, queue):
    from io import BytesIO

    csv_content = b"name,value\nAlice,10\nBob,20"
    csv_file = BytesIO(csv_content)
    csv_file.name = "test.csv"

    url = reverse("human_annotations:queue_import_csv", args=[team_with_users.slug, queue.pk])
    response = client.post(url, {"csv_file": csv_file})
    assert response.status_code == 302

    items = AnnotationItem.objects.filter(queue=queue, item_type=AnnotationItemType.EXTERNAL)
    assert items.count() == 2
    assert items.first().external_data["name"] == "Alice"


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
def test_flag_item(client, team_with_users, queue):
    item = AnnotationItemFactory(queue=queue, team=team_with_users)
    url = reverse(
        "human_annotations:flag_item",
        args=[team_with_users.slug, queue.pk, item.pk],
    )
    response = client.post(url)
    assert response.status_code == 302
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
