from unittest.mock import patch

import pytest
from django.test import Client, override_settings
from django.urls import reverse

from apps.chat.models import ChatMessageType
from apps.evaluations.forms import EvaluationDatasetForm, ImportFromAnnotationQueueForm
from apps.evaluations.models import (
    DatasetCreationStatus,
    EvaluationDataset,
    EvaluationMessage,
    EvaluationMode,
)
from apps.evaluations.tasks import create_dataset_from_sessions_task
from apps.human_annotations.models import AnnotationItem, QueueStatus
from apps.utils.factories.experiment import ChatFactory, ChatMessageFactory, ExperimentSessionFactory
from apps.utils.factories.human_annotations import AnnotationItemFactory, AnnotationQueueFactory
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def team_with_users(db):
    return TeamWithUsersFactory.create()


@pytest.fixture()
def user(team_with_users):
    return team_with_users.members.first()


@pytest.fixture()
def client_with_user(user):
    c = Client()
    c.force_login(user)
    return c


@pytest.fixture()
def session_dataset(team_with_users):
    return EvaluationDataset.objects.create(
        team=team_with_users,
        name="Test Session Dataset",
        evaluation_mode=EvaluationMode.SESSION,
    )


@pytest.fixture()
def message_dataset(team_with_users):
    return EvaluationDataset.objects.create(
        team=team_with_users,
        name="Test Message Dataset",
        evaluation_mode=EvaluationMode.MESSAGE,
    )


@pytest.fixture()
def queue_with_session_items(team_with_users, user):
    queue = AnnotationQueueFactory.create(team=team_with_users, created_by=user)
    session = ExperimentSessionFactory.create(team=team_with_users)
    ChatMessageFactory.create(message_type=ChatMessageType.HUMAN, content="Hello", chat=session.chat)
    ChatMessageFactory.create(message_type=ChatMessageType.AI, content="Hi!", chat=session.chat)
    AnnotationItemFactory.create(queue=queue, team=team_with_users, session=session)
    return queue, session


# === Form ===


@pytest.mark.django_db()
def test_form_only_shows_queues_with_session_items(team_with_users, user):
    queue_with_sessions = AnnotationQueueFactory.create(team=team_with_users, created_by=user, name="With Sessions")
    session = ExperimentSessionFactory.create(team=team_with_users)
    AnnotationItemFactory.create(queue=queue_with_sessions, team=team_with_users, session=session)

    AnnotationQueueFactory.create(team=team_with_users, created_by=user, name="Empty Queue")

    other_team = TeamWithUsersFactory.create()
    other_queue = AnnotationQueueFactory.create(team=other_team)
    other_session = ExperimentSessionFactory.create(team=other_team)
    AnnotationItemFactory.create(queue=other_queue, team=other_team, session=other_session)

    form = ImportFromAnnotationQueueForm(team=team_with_users)
    queue_ids = set(form.fields["queue"].queryset.values_list("id", flat=True))
    assert queue_ids == {queue_with_sessions.id}


@pytest.mark.django_db()
def test_form_excludes_archived_queues(team_with_users, user):
    queue = AnnotationQueueFactory.create(team=team_with_users, created_by=user, status=QueueStatus.ARCHIVED)
    session = ExperimentSessionFactory.create(team=team_with_users)
    AnnotationItemFactory.create(queue=queue, team=team_with_users, session=session)

    form = ImportFromAnnotationQueueForm(team=team_with_users)
    assert form.fields["queue"].queryset.count() == 0


@pytest.mark.django_db()
def test_form_is_valid_when_queue_selected(team_with_users, user, queue_with_session_items):
    queue, _ = queue_with_session_items
    form = ImportFromAnnotationQueueForm({"queue": queue.id}, team=team_with_users)
    assert form.is_valid(), form.errors


@pytest.mark.django_db()
def test_form_is_invalid_when_no_queue_selected(team_with_users, user):
    form = ImportFromAnnotationQueueForm({}, team=team_with_users)
    assert not form.is_valid()


# === View ===


@pytest.mark.django_db()
def test_get_renders_form(client_with_user, team_with_users, session_dataset):
    url = reverse("evaluations:dataset_import_from_queue", args=[team_with_users.slug, session_dataset.pk])
    response = client_with_user.get(url)
    assert response.status_code == 200
    assert "form" in response.context
    assert "dataset" in response.context


@pytest.mark.django_db()
def test_get_404_for_message_mode_dataset(client_with_user, team_with_users, message_dataset):
    """Endpoint is session-mode only; message-mode datasets 404."""
    url = reverse("evaluations:dataset_import_from_queue", args=[team_with_users.slug, message_dataset.pk])
    response = client_with_user.get(url)
    assert response.status_code == 404


@pytest.mark.django_db()
def test_post_invalid_form_rerenders(client_with_user, team_with_users, session_dataset):
    url = reverse("evaluations:dataset_import_from_queue", args=[team_with_users.slug, session_dataset.pk])
    response = client_with_user.post(url, {"queue": ""})
    assert response.status_code == 200
    assert response.context["form"].errors


@pytest.mark.django_db()
def test_post_dispatches_task_with_session_external_ids(
    client_with_user, team_with_users, session_dataset, queue_with_session_items
):
    queue, session = queue_with_session_items

    url = reverse("evaluations:dataset_import_from_queue", args=[team_with_users.slug, session_dataset.pk])

    with patch("apps.evaluations.views.dataset_views.create_dataset_from_sessions_task") as mock_task:
        mock_task.delay.return_value.id = "fake-task-id"
        response = client_with_user.post(url, {"queue": queue.id})

    assert response.status_code == 302
    mock_task.delay.assert_called_once()
    args = mock_task.delay.call_args.args
    assert args[0] == session_dataset.id
    assert args[1] == team_with_users.id
    assert args[2] == [str(session.external_id)]

    session_dataset.refresh_from_db()
    assert session_dataset.status == DatasetCreationStatus.PENDING
    assert session_dataset.job_id == "fake-task-id"


@pytest.mark.django_db()
def test_post_with_empty_queue_does_not_dispatch_task(client_with_user, team_with_users, session_dataset, user):
    """A queue with no session items shouldn't be selectable, but defensively the view skips dispatch."""
    queue = AnnotationQueueFactory.create(team=team_with_users, created_by=user)
    session = ExperimentSessionFactory.create(team=team_with_users)
    item = AnnotationItemFactory.create(queue=queue, team=team_with_users, session=session)

    url = reverse("evaluations:dataset_import_from_queue", args=[team_with_users.slug, session_dataset.pk])

    item.session = None
    item.save(update_fields=["session"])

    with patch("apps.evaluations.views.dataset_views.create_dataset_from_sessions_task") as mock_task:
        response = client_with_user.post(url, {"queue": queue.id})

    # The form rejects this queue (it has no session items), so it re-renders.
    assert response.status_code == 200
    mock_task.delay.assert_not_called()


@pytest.mark.django_db()
def test_idempotent_import_via_task(team_with_users, user, session_dataset, queue_with_session_items):
    """Re-importing the same session through the task is a no-op."""
    queue, session = queue_with_session_items

    existing_message = EvaluationMessage.objects.create(
        input={},
        output={},
        history=[],
        session=session,
        metadata={"session_id": str(session.external_id), "created_mode": "clone"},
    )
    session_dataset.messages.add(existing_message)

    with override_settings(CELERY_TASK_ALWAYS_EAGER=True):
        result = create_dataset_from_sessions_task.delay(
            session_dataset.id, team_with_users.id, [str(session.external_id)]
        ).get()

    assert result["success"] is True
    assert result["created_count"] == 0
    assert result["duplicates_skipped"] == 1
    session_dataset.refresh_from_db()
    assert session_dataset.messages.count() == 1
    assert session_dataset.status == DatasetCreationStatus.COMPLETED


@pytest.mark.django_db()
def test_post_requires_team_match(team_with_users, user):
    other_team = TeamWithUsersFactory.create()
    other_session_dataset = EvaluationDataset.objects.create(
        team=other_team, name="Other Team Dataset", evaluation_mode=EvaluationMode.SESSION
    )
    client = Client()
    client.force_login(user)

    url = reverse("evaluations:dataset_import_from_queue", args=[team_with_users.slug, other_session_dataset.pk])
    response = client.get(url)
    assert response.status_code == 404


@pytest.mark.django_db()
def test_dataset_external_id_passed_as_string(client_with_user, team_with_users, session_dataset, user):
    """Ensure external_ids are passed as strings (UUIDs) for celery serialization."""
    queue = AnnotationQueueFactory.create(team=team_with_users, created_by=user)
    session1 = ExperimentSessionFactory.create(team=team_with_users)
    session2 = ExperimentSessionFactory.create(team=team_with_users)
    AnnotationItemFactory.create(queue=queue, team=team_with_users, session=session1)
    AnnotationItemFactory.create(queue=queue, team=team_with_users, session=session2)

    url = reverse("evaluations:dataset_import_from_queue", args=[team_with_users.slug, session_dataset.pk])

    with patch("apps.evaluations.views.dataset_views.create_dataset_from_sessions_task") as mock_task:
        mock_task.delay.return_value.id = "fake-task-id"
        client_with_user.post(url, {"queue": queue.id})

    args = mock_task.delay.call_args.args
    passed_ids = set(args[2])
    assert passed_ids == {str(session1.external_id), str(session2.external_id)}
    assert all(isinstance(x, str) for x in args[2])


@pytest.mark.django_db()
def test_post_excludes_message_only_items(client_with_user, team_with_users, session_dataset, user):
    """Items with item_type=message (no session FK) should not be imported."""
    queue = AnnotationQueueFactory.create(team=team_with_users, created_by=user)
    session = ExperimentSessionFactory.create(team=team_with_users)
    AnnotationItemFactory.create(queue=queue, team=team_with_users, session=session)

    # Create a message-only item directly (no session FK)
    chat = ChatFactory.create(team=team_with_users)
    chat_message = ChatMessageFactory.create(message_type=ChatMessageType.HUMAN, content="Hello", chat=chat)
    AnnotationItem.objects.create(
        queue=queue,
        team=team_with_users,
        item_type="message",
        message=chat_message,
    )

    url = reverse("evaluations:dataset_import_from_queue", args=[team_with_users.slug, session_dataset.pk])

    with patch("apps.evaluations.views.dataset_views.create_dataset_from_sessions_task") as mock_task:
        mock_task.delay.return_value.id = "fake-task-id"
        client_with_user.post(url, {"queue": queue.id})

    args = mock_task.delay.call_args.args
    assert args[2] == [str(session.external_id)]


# === Create dataset with annotation_queue mode ===


@pytest.mark.django_db()
def test_create_form_annotation_queue_mode_session_dataset(team_with_users, queue_with_session_items):
    """A session-mode dataset can be created by importing from an annotation queue."""
    queue, session = queue_with_session_items

    form = EvaluationDatasetForm(
        team=team_with_users,
        data={
            "name": "Queue Session Dataset",
            "evaluation_mode": "session",
            "mode": "annotation_queue",
            "annotation_queue": queue.id,
        },
    )
    assert form.is_valid(), form.errors
    form.instance.team = team_with_users

    with (
        patch.object(form, "_save_sessions_clone") as mock_session_clone,
        patch.object(form, "_save_session_messages_clone") as mock_msg_clone,
    ):
        form.save()

    mock_session_clone.assert_called_once()
    mock_msg_clone.assert_not_called()
    assert form.cleaned_data["session_ids"] == {str(session.external_id)}


@pytest.mark.django_db()
def test_create_form_annotation_queue_mode_message_dataset(team_with_users, queue_with_session_items):
    """A message-mode dataset can also be created by importing sessions from an annotation queue."""
    queue, session = queue_with_session_items

    form = EvaluationDatasetForm(
        team=team_with_users,
        data={
            "name": "Queue Message Dataset",
            "evaluation_mode": "message",
            "mode": "annotation_queue",
            "annotation_queue": queue.id,
        },
    )
    assert form.is_valid(), form.errors
    form.instance.team = team_with_users

    with (
        patch.object(form, "_save_sessions_clone") as mock_session_clone,
        patch.object(form, "_save_session_messages_clone") as mock_msg_clone,
    ):
        form.save()

    mock_msg_clone.assert_called_once()
    mock_session_clone.assert_not_called()
    assert form.cleaned_data["session_ids"] == {str(session.external_id)}


@pytest.mark.django_db()
def test_create_form_annotation_queue_requires_queue_with_sessions(team_with_users, user):
    """An empty queue (no session items) is not selectable in the queryset."""
    empty_queue = AnnotationQueueFactory.create(team=team_with_users, created_by=user)

    form = EvaluationDatasetForm(
        team=team_with_users,
        data={
            "name": "Bad Queue Dataset",
            "evaluation_mode": "session",
            "mode": "annotation_queue",
            "annotation_queue": empty_queue.id,
        },
    )
    assert not form.is_valid()
    assert "annotation_queue" in form.errors


@pytest.mark.django_db()
def test_create_form_annotation_queue_field_filters_to_team(team_with_users, user):
    """The annotation_queue queryset is scoped to the user's team."""
    other_team = TeamWithUsersFactory.create()
    other_queue = AnnotationQueueFactory.create(team=other_team)
    other_session = ExperimentSessionFactory.create(team=other_team)
    AnnotationItemFactory.create(queue=other_queue, team=other_team, session=other_session)

    own_queue = AnnotationQueueFactory.create(team=team_with_users, created_by=user)
    own_session = ExperimentSessionFactory.create(team=team_with_users)
    AnnotationItemFactory.create(queue=own_queue, team=team_with_users, session=own_session)

    form = EvaluationDatasetForm(team=team_with_users)
    queue_ids = set(form.fields["annotation_queue"].queryset.values_list("id", flat=True))
    assert queue_ids == {own_queue.id}


# === Session selection table excludes sessions already in dataset ===


@pytest.mark.django_db()
def test_session_selection_list_excludes_sessions_already_in_dataset(
    client_with_user, team_with_users, session_dataset
):
    """When editing a dataset, sessions already linked to it are excluded from the selection table."""
    in_dataset = ExperimentSessionFactory.create(team=team_with_users)
    not_in_dataset = ExperimentSessionFactory.create(team=team_with_users)

    msg = EvaluationMessage.objects.create(
        input={}, output={}, history=[], session=in_dataset, metadata={"session_id": str(in_dataset.external_id)}
    )
    session_dataset.messages.add(msg)

    url = reverse("evaluations:dataset_sessions_selection_json", args=[team_with_users.slug])
    response = client_with_user.get(url, {"dataset_id": session_dataset.pk})

    assert response.status_code == 200
    session_ids = response.json()
    assert str(not_in_dataset.external_id) in session_ids
    assert str(in_dataset.external_id) not in session_ids


@pytest.mark.django_db()
def test_session_selection_list_without_dataset_id_returns_all(client_with_user, team_with_users, session_dataset):
    """Without a dataset_id parameter, all team sessions are returned (create flow)."""
    in_dataset = ExperimentSessionFactory.create(team=team_with_users)
    other = ExperimentSessionFactory.create(team=team_with_users)

    msg = EvaluationMessage.objects.create(
        input={}, output={}, history=[], session=in_dataset, metadata={"session_id": str(in_dataset.external_id)}
    )
    session_dataset.messages.add(msg)

    url = reverse("evaluations:dataset_sessions_selection_json", args=[team_with_users.slug])
    response = client_with_user.get(url)

    assert response.status_code == 200
    session_ids = response.json()
    assert str(in_dataset.external_id) in session_ids
    assert str(other.external_id) in session_ids


@pytest.mark.django_db()
def test_session_selection_list_ignores_invalid_dataset_id(client_with_user, team_with_users):
    """A non-integer dataset_id param should not raise; just return the unfiltered list."""
    session = ExperimentSessionFactory.create(team=team_with_users)

    url = reverse("evaluations:dataset_sessions_selection_json", args=[team_with_users.slug])
    response = client_with_user.get(url, {"dataset_id": "not-an-int"})

    assert response.status_code == 200
    assert str(session.external_id) in response.json()
