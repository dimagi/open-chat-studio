from unittest.mock import patch

import pytest
from django.test import override_settings

from apps.chat.models import ChatMessageType
from apps.evaluations.models import DatasetCreationStatus, EvaluationDataset, EvaluationMessage, EvaluationMode
from apps.evaluations.tasks import _sessions_for_session_mode_clone, create_dataset_from_sessions_task
from apps.utils.factories.experiment import ChatMessageFactory, ExperimentFactory, ExperimentSessionFactory


def _session_with_messages(team=None, experiment=None):
    kwargs = {}
    if team is not None:
        kwargs["team"] = team
    if experiment is not None:
        kwargs["experiment"] = experiment
    session = ExperimentSessionFactory.create(**kwargs)
    ChatMessageFactory.create(message_type=ChatMessageType.HUMAN, content="Hello", chat=session.chat)
    ChatMessageFactory.create(message_type=ChatMessageType.AI, content="Hi!", chat=session.chat)
    return session


@pytest.mark.django_db()
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
def test_create_dataset_from_sessions_task_success():
    """Task creates one EvaluationMessage per session and sets status to COMPLETED."""
    session = ExperimentSessionFactory.create()
    team = session.team

    ChatMessageFactory.create(message_type=ChatMessageType.HUMAN, content="Hello", chat=session.chat)
    ChatMessageFactory.create(message_type=ChatMessageType.AI, content="Hi!", chat=session.chat)

    dataset = EvaluationDataset.objects.create(
        team=team, name="Test Session Dataset", evaluation_mode=EvaluationMode.SESSION
    )

    task_result = create_dataset_from_sessions_task.delay(dataset.id, team.id, [session.external_id])
    result = task_result.get()

    assert result["success"] is True
    assert result["created_count"] == 1
    assert result["duplicates_skipped"] == 0

    dataset.refresh_from_db()
    assert dataset.status == DatasetCreationStatus.COMPLETED
    assert dataset.job_id == ""

    messages = list(dataset.messages.select_related("session").all())
    assert len(messages) == 1
    assert messages[0].input == {}
    assert messages[0].output == {}
    assert len(messages[0].history) == 2
    assert messages[0].session == session


@pytest.mark.django_db()
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
def test_create_dataset_from_sessions_task_all_duplicates():
    """Task returns success with 0 created_count when all sessions are already in the dataset."""
    session = ExperimentSessionFactory.create()
    team = session.team

    ChatMessageFactory.create(message_type=ChatMessageType.HUMAN, content="Hello", chat=session.chat)
    ChatMessageFactory.create(message_type=ChatMessageType.AI, content="Hi!", chat=session.chat)

    existing_message = EvaluationMessage.objects.create(
        input={},
        output={},
        history=[],
        session=session,
        metadata={"session_id": str(session.external_id), "created_mode": "clone"},
    )
    dataset = EvaluationDataset.objects.create(
        team=team, name="Test Session Dataset 2", evaluation_mode=EvaluationMode.SESSION
    )
    dataset.messages.add(existing_message)

    task_result = create_dataset_from_sessions_task.delay(dataset.id, team.id, [session.external_id])
    result = task_result.get()

    assert result["success"] is True
    assert result["created_count"] == 0
    assert result["duplicates_skipped"] == 1

    dataset.refresh_from_db()
    assert dataset.status == DatasetCreationStatus.COMPLETED
    # Original message still there, no duplicate added
    assert dataset.messages.count() == 1


@pytest.mark.django_db()
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
def test_create_dataset_from_sessions_task_dataset_not_found():
    """Task returns error dict when dataset doesn't exist."""
    session = ExperimentSessionFactory.create()
    team = session.team

    task_result = create_dataset_from_sessions_task.delay(99999, team.id, [session.external_id])
    result = task_result.get()

    assert result["success"] is False
    assert "error" in result


@pytest.mark.django_db()
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
def test_create_dataset_from_sessions_task_error_path():
    """Task handles exceptions by saving FAILED status to dataset."""
    session = ExperimentSessionFactory.create()
    team = session.team

    ChatMessageFactory.create(message_type=ChatMessageType.HUMAN, content="Hello", chat=session.chat)
    ChatMessageFactory.create(message_type=ChatMessageType.AI, content="Hi!", chat=session.chat)

    dataset = EvaluationDataset.objects.create(
        team=team, name="Test Session Dataset 3", evaluation_mode=EvaluationMode.SESSION
    )

    with patch(
        "apps.evaluations.tasks.iter_session_evaluation_messages_for_sessions", side_effect=Exception("DB error")
    ):
        task_result = create_dataset_from_sessions_task.delay(dataset.id, team.id, [session.external_id])
        result = task_result.get()

    assert result["success"] is False

    dataset.refresh_from_db()
    assert dataset.status == DatasetCreationStatus.FAILED
    # The real exception reaches the user, not a fixed string, and names what was committed.
    assert "DB error" in dataset.error_message
    assert "0 of 1 session(s)" in dataset.error_message


@pytest.mark.django_db()
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
def test_task_resolves_filter_query_instead_of_session_ids():
    """A stored filter is resolved inside the task, so the caller posts no session ids at all."""
    experiment = ExperimentFactory.create()
    team = experiment.team
    matching = _session_with_messages(team=team, experiment=experiment)
    other = _session_with_messages(team=team, experiment=ExperimentFactory.create(team=team))

    dataset = EvaluationDataset.objects.create(
        team=team, name="Filtered Session Dataset", evaluation_mode=EvaluationMode.SESSION
    )

    task_result = create_dataset_from_sessions_task.delay(
        dataset.id,
        team.id,
        None,
        f"f_experiment={experiment.id}&op_experiment=any+of",
        None,
    )
    result = task_result.get()

    assert result["success"] is True
    assert result["created_count"] == 1
    session_ids = {message.session_id for message in dataset.messages.all()}
    assert session_ids == {matching.id}
    assert other.id not in session_ids


@pytest.mark.django_db()
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
def test_empty_filter_query_means_every_team_session():
    """ "" is "no filters applied", which must stay distinct from None ("use the id list")."""
    session = _session_with_messages()
    team = session.team
    dataset = EvaluationDataset.objects.create(
        team=team, name="Unfiltered Session Dataset", evaluation_mode=EvaluationMode.SESSION
    )

    result = create_dataset_from_sessions_task.delay(dataset.id, team.id, None, "", None).get()

    assert result["created_count"] == 1
    assert dataset.messages.get().session_id == session.id


@pytest.mark.django_db()
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
def test_filter_query_excludes_sessions_already_in_dataset():
    """Re-running the same filter adds nothing: matches already in the dataset are excluded."""
    session = _session_with_messages()
    team = session.team
    dataset = EvaluationDataset.objects.create(
        team=team, name="Idempotent Session Dataset", evaluation_mode=EvaluationMode.SESSION
    )

    first = create_dataset_from_sessions_task.delay(dataset.id, team.id, None, "", None).get()
    second = create_dataset_from_sessions_task.delay(dataset.id, team.id, None, "", None).get()

    assert first["created_count"] == 1
    assert second["created_count"] == 0
    assert second["duplicates_skipped"] == 0  # excluded by the query, not deduped after the fact
    assert dataset.messages.count() == 1


@pytest.mark.django_db()
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
def test_no_sessions_and_no_filter_completes_without_error():
    """Neither a filter nor ids means there is nothing to do — not a failure."""
    session = _session_with_messages()
    dataset = EvaluationDataset.objects.create(
        team=session.team, name="Nothing To Do", evaluation_mode=EvaluationMode.SESSION
    )

    result = create_dataset_from_sessions_task.delay(dataset.id, session.team_id).get()

    assert result["success"] is True
    assert result["created_count"] == 0
    dataset.refresh_from_db()
    assert dataset.status == DatasetCreationStatus.COMPLETED
    assert dataset.messages.count() == 0


@pytest.mark.django_db()
def test_pk_ceiling_snapshots_the_filter_result_set():
    """A session created after the job resolved its filter must not appear in the walk.

    The ceiling is what keeps the up-front count and the walk over the same set of rows: both
    re-run the filter, and a relative range filter re-resolves against a moving now().
    """
    session = _session_with_messages()
    team = session.team
    dataset = EvaluationDataset.objects.create(
        team=team, name="Ceiling Session Dataset", evaluation_mode=EvaluationMode.SESSION
    )

    queryset = _sessions_for_session_mode_clone(dataset, None, "", None)
    late_session = _session_with_messages(team=team)

    assert late_session.pk > session.pk
    assert list(queryset.values_list("pk", flat=True)) == [session.pk]
