"""The message-mode clone task resolves a stored session filter server-side instead of a posted id list."""

import pytest
from django.test import override_settings

from apps.chat.models import ChatMessageType
from apps.evaluations.models import DatasetCreationStatus
from apps.evaluations.tasks import create_dataset_from_session_messages_task
from apps.evaluations.tests.clone_helpers import message_dataset, session_with_messages
from apps.utils.factories.experiment import ChatMessageFactory, ExperimentFactory, ExperimentSessionFactory
from apps.web.dynamic_filters.datastructures import ColumnFilterData, FilterParams


def experiment_filter(experiment) -> str:
    return f"f_experiment={experiment.id}&op_experiment=any+of"


@pytest.mark.django_db()
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
def test_task_resolves_session_filter_instead_of_ids():
    """A stored session filter is resolved inside the task, so the caller posts no session ids."""
    experiment = ExperimentFactory.create()
    team = experiment.team
    matching = session_with_messages(team=team, experiment=experiment)
    session_with_messages(team=team, experiment=ExperimentFactory.create(team=team))
    dataset = message_dataset(team)

    result = create_dataset_from_session_messages_task.delay(
        dataset.id, team.id, [], [], None, None, session_filter_query=experiment_filter(experiment)
    ).get()

    assert result["created_count"] == 1
    assert {message.session_id for message in dataset.messages.all()} == {matching.id}


@pytest.mark.django_db()
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
def test_empty_session_filter_means_every_team_session():
    """An empty session filter means every team session, which must stay distinct from None (use the id list)."""
    session = session_with_messages()
    team = session.team
    dataset = message_dataset(team)

    result = create_dataset_from_session_messages_task.delay(
        dataset.id, team.id, [], [], None, None, session_filter_query=""
    ).get()

    assert result["created_count"] == 1
    assert dataset.messages.get().session_id == session.id


@pytest.mark.django_db()
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
def test_session_filter_excludes_sessions_already_in_dataset():
    """Re-running the same filter adds nothing: sessions already in the dataset are excluded by the query."""
    session = session_with_messages()
    team = session.team
    dataset = message_dataset(team)

    first = create_dataset_from_session_messages_task.delay(
        dataset.id, team.id, [], [], None, None, session_filter_query=""
    ).get()
    second = create_dataset_from_session_messages_task.delay(
        dataset.id, team.id, [], [], None, None, session_filter_query=""
    ).get()

    assert (first["created_count"], second["created_count"], second["duplicates_skipped"]) == (1, 0, 0)
    assert dataset.messages.count() == 1


@pytest.mark.django_db()
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
def test_session_filter_with_message_filter_applies_both():
    """The session filter picks the sessions and the message filter picks the pairs within them."""
    experiment = ExperimentFactory.create()
    team = experiment.team
    matching = ExperimentSessionFactory.create(team=team, experiment=experiment)
    other = ExperimentSessionFactory.create(team=team, experiment=ExperimentFactory.create(team=team))
    for session, content, rating in ((matching, "good", "+1"), (matching, "bad", "-1"), (other, "other", "+1")):
        human = ChatMessageFactory.create(message_type=ChatMessageType.HUMAN, content=content, chat=session.chat)
        human.add_rating(rating)
        ChatMessageFactory.create(message_type=ChatMessageType.AI, content=f"{content} reply", chat=session.chat)
    filter_query = FilterParams(
        column_filters=[ColumnFilterData(column="tags", operator="any_of", value='["+1"]')]
    ).to_query()
    dataset = message_dataset(team)

    result = create_dataset_from_session_messages_task.delay(
        dataset.id, team.id, [], [], filter_query, None, session_filter_query=experiment_filter(experiment)
    ).get()

    assert result["created_count"] == 1
    assert dataset.messages.get().input["content"] == "good"


@pytest.mark.django_db()
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
def test_session_filter_with_no_matches_completes():
    """A filter that matches no sessions is an empty clone, not a failure."""
    session = session_with_messages()
    team = session.team
    empty_experiment = ExperimentFactory.create(team=team)
    dataset = message_dataset(team)

    result = create_dataset_from_session_messages_task.delay(
        dataset.id, team.id, [], [], None, None, session_filter_query=experiment_filter(empty_experiment)
    ).get()

    assert result == {"success": True, "created_count": 0, "duplicates_skipped": 0}
    dataset.refresh_from_db()
    assert dataset.status == DatasetCreationStatus.COMPLETED


@pytest.mark.django_db()
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
def test_task_ignores_session_ids_from_other_teams():
    """A session id from another team is never read, even when it is passed in."""
    foreign = session_with_messages()
    team = ExperimentFactory.create().team
    dataset = message_dataset(team)

    result = create_dataset_from_session_messages_task.delay(
        dataset.id, team.id, [foreign.external_id], [], None, None
    ).get()

    assert result["created_count"] == 0
    dataset.refresh_from_db()
    assert dataset.status == DatasetCreationStatus.COMPLETED


@pytest.mark.django_db()
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
def test_task_regular_ids_ignore_the_message_filter():
    """Ids sent as regular sessions keep every pair, even when a message filter travels with them."""
    session = ExperimentSessionFactory.create()
    for content, rating in (("good", "+1"), ("bad", "-1")):
        human = ChatMessageFactory.create(message_type=ChatMessageType.HUMAN, content=content, chat=session.chat)
        human.add_rating(rating)
        ChatMessageFactory.create(message_type=ChatMessageType.AI, content=f"{content} reply", chat=session.chat)
    filter_query = FilterParams(
        column_filters=[ColumnFilterData(column="tags", operator="any_of", value='["+1"]')]
    ).to_query()
    dataset = message_dataset(session.team)

    result = create_dataset_from_session_messages_task.delay(
        dataset.id, session.team.id, [session.external_id], [], filter_query, None
    ).get()

    assert result["created_count"] == 2
    assert sorted(message.input["content"] for message in dataset.messages.all()) == ["bad", "good"]
