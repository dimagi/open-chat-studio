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
from apps.human_annotations.models import QueueStatus
from apps.teams.utils import current_team
from apps.utils.factories.experiment import ChatMessageFactory, ExperimentSessionFactory
from apps.utils.factories.human_annotations import AnnotationItemFactory, AnnotationQueueFactory
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def team_with_users(db):
    return TeamWithUsersFactory.create()


@pytest.fixture()
def user(team_with_users):
    return team_with_users.members.first()


@pytest.fixture()
def team_context(team_with_users):
    """Sets the team in the contextvar so TeamBackend can resolve membership-based perms.

    Required for tests that instantiate forms directly (no request) and rely on
    user.has_perm() inside AnnotationQueue.objects.visible_to().
    """
    with current_team(team_with_users):
        yield team_with_users


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
def queue_with_session_items(team_with_users, user):
    queue = AnnotationQueueFactory.create(team=team_with_users, created_by=user)
    session = ExperimentSessionFactory.create(team=team_with_users)
    ChatMessageFactory.create(message_type=ChatMessageType.HUMAN, content="Hello", chat=session.chat)
    ChatMessageFactory.create(message_type=ChatMessageType.AI, content="Hi!", chat=session.chat)
    AnnotationItemFactory.create(queue=queue, team=team_with_users, session=session)
    return queue, session


# === Form ===


@pytest.mark.django_db()
def test_form_only_shows_queues_with_session_items(team_with_users, user, team_context):
    queue_with_sessions = AnnotationQueueFactory.create(team=team_with_users, created_by=user, name="With Sessions")
    session = ExperimentSessionFactory.create(team=team_with_users)
    AnnotationItemFactory.create(queue=queue_with_sessions, team=team_with_users, session=session)

    AnnotationQueueFactory.create(team=team_with_users, created_by=user, name="Empty Queue")

    other_team = TeamWithUsersFactory.create()
    other_queue = AnnotationQueueFactory.create(team=other_team)
    other_session = ExperimentSessionFactory.create(team=other_team)
    AnnotationItemFactory.create(queue=other_queue, team=other_team, session=other_session)

    form = ImportFromAnnotationQueueForm(team=team_with_users, user=user)
    queue_ids = set(form.fields["queue"].queryset.values_list("id", flat=True))
    assert queue_ids == {queue_with_sessions.id}


@pytest.mark.django_db()
def test_form_excludes_archived_queues(team_with_users, user, team_context):
    queue = AnnotationQueueFactory.create(team=team_with_users, created_by=user, status=QueueStatus.ARCHIVED)
    session = ExperimentSessionFactory.create(team=team_with_users)
    AnnotationItemFactory.create(queue=queue, team=team_with_users, session=session)

    form = ImportFromAnnotationQueueForm(team=team_with_users, user=user)
    assert form.fields["queue"].queryset.count() == 0


@pytest.mark.django_db()
def test_form_uses_visible_to_when_user_passed(team_with_users, user, queue_with_session_items, team_context):
    """Form delegates to AnnotationQueue.objects.visible_to when a user is provided.

    visible_to returns all team queues for users with add_annotationqueue perm
    (which TeamWithUsersFactory admins have), so the queue stays visible.
    """
    queue, _ = queue_with_session_items
    form = ImportFromAnnotationQueueForm(team=team_with_users, user=user)
    assert queue.id in set(form.fields["queue"].queryset.values_list("id", flat=True))


# === View ===


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
def test_post_handles_celery_enqueue_failure(
    client_with_user, team_with_users, session_dataset, queue_with_session_items
):
    """If Celery enqueue raises, the dataset is marked FAILED instead of stuck in PENDING."""
    queue, _ = queue_with_session_items

    url = reverse("evaluations:dataset_import_from_queue", args=[team_with_users.slug, session_dataset.pk])

    with patch("apps.evaluations.views.dataset_views.create_dataset_from_sessions_task") as mock_task:
        mock_task.delay.side_effect = RuntimeError("broker unavailable")
        response = client_with_user.post(url, {"queue": queue.id})

    assert response.status_code == 302
    session_dataset.refresh_from_db()
    assert session_dataset.status == DatasetCreationStatus.FAILED
    assert session_dataset.error_message
    assert session_dataset.job_id == ""


# === Create dataset with annotation_queue mode ===


@pytest.mark.django_db()
def test_create_form_annotation_queue_field_filters_to_team(team_with_users, user, team_context):
    """The annotation_queue queryset is scoped to the user's team."""
    other_team = TeamWithUsersFactory.create()
    other_queue = AnnotationQueueFactory.create(team=other_team)
    other_session = ExperimentSessionFactory.create(team=other_team)
    AnnotationItemFactory.create(queue=other_queue, team=other_team, session=other_session)

    own_queue = AnnotationQueueFactory.create(team=team_with_users, created_by=user)
    own_session = ExperimentSessionFactory.create(team=team_with_users)
    AnnotationItemFactory.create(queue=own_queue, team=team_with_users, session=own_session)

    form = EvaluationDatasetForm(team=team_with_users, user=user)
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


# ===== dataset_sessions_count + EvalDatasetAddSessionsView (issue #3354) =====

@pytest.mark.django_db()
def test_dataset_sessions_count_returns_total(client_with_user, team_with_users, session_dataset):
    """Count endpoint returns number of sessions not already in the dataset."""
    from apps.chat.models import ChatMessage as CM
    s1 = ExperimentSessionFactory.create(team=team_with_users)
    s2 = ExperimentSessionFactory.create(team=team_with_users)
    # s2 is already in the dataset
    msg = EvaluationMessage.objects.create(
        input={}, output={}, history=[], session=s2,
        metadata={"session_id": str(s2.external_id)}
    )
    session_dataset.messages.add(msg)

    url = reverse("evaluations:dataset_sessions_count", args=[team_with_users.slug, session_dataset.pk])
    response = client_with_user.get(url)
    assert response.status_code == 200
    data = response.json()
    assert "ids" not in data
    assert "total" in data
    # s1 is available, s2 is excluded
    assert data["total"] >= 1


@pytest.mark.django_db()
def test_dataset_sessions_count_requires_login(team_with_users, session_dataset):
    from django.test import Client as DjangoClient
    c = DjangoClient()
    url = reverse("evaluations:dataset_sessions_count", args=[team_with_users.slug, session_dataset.pk])
    response = c.get(url)
    assert response.status_code in (302, 403)


@pytest.mark.django_db()
def test_eval_dataset_add_sessions_get(client_with_user, team_with_users, session_dataset):
    """GET renders the add-sessions sub-page."""
    url = reverse("evaluations:dataset_add_sessions", args=[team_with_users.slug, session_dataset.pk])
    response = client_with_user.get(url)
    assert response.status_code == 200
    assert "sessions_count_url" in response.context
    assert "dataset" in response.context


@pytest.mark.django_db()
def test_eval_dataset_add_sessions_post_selected(client_with_user, team_with_users, session_dataset):
    """POST with mode=selected and valid session IDs redirects to dataset edit."""
    session = ExperimentSessionFactory.create(team=team_with_users)
    url = reverse("evaluations:dataset_add_sessions", args=[team_with_users.slug, session_dataset.pk])
    response = client_with_user.post(url, {
        "mode": "selected",
        "session_ids": str(session.external_id),
        "message_scope": "all",
    })
    assert response.status_code == 302
    assert response["Location"].endswith(
        reverse("evaluations:dataset_edit", args=[team_with_users.slug, session_dataset.pk])
    )


@pytest.mark.django_db()
def test_eval_dataset_add_sessions_post_no_sessions_stays_on_page(client_with_user, team_with_users, session_dataset):
    """POST with no sessions redirects back to add-sessions page with an error."""
    url = reverse("evaluations:dataset_add_sessions", args=[team_with_users.slug, session_dataset.pk])
    response = client_with_user.post(url, {"mode": "selected", "session_ids": "", "message_scope": "all"})
    assert response.status_code == 302
    assert "add-sessions" in response["Location"]
