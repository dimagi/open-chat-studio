"""Message-mode generators stream pairs a chunk of sessions at a time; the clone task commits, reports and resumes."""

import itertools
from unittest.mock import patch

import pytest
from django.test import override_settings

from apps.chat.models import ChatMessage, ChatMessageType
from apps.evaluations.models import DatasetCreationStatus, EvaluationDataset, EvaluationMode
from apps.evaluations.tasks import create_dataset_from_session_messages_task
from apps.evaluations.tests.clone_helpers import make_sessions
from apps.evaluations.utils import (
    iter_message_evaluation_chunks,
    iter_message_evaluation_messages,
    iter_message_evaluation_messages_for_sessions,
    make_evaluation_messages_from_sessions,
)
from apps.experiments.models import ExperimentSession
from apps.utils.factories.experiment import ChatMessageFactory, ExperimentSessionFactory
from apps.web.dynamic_filters.datastructures import ColumnFilterData, FilterParams

POSITIVE_FILTER = FilterParams(column_filters=[ColumnFilterData(column="tags", operator="any_of", value='["+1"]')])


def add_rated_pair(session, content, rating):
    human = ChatMessageFactory.create(message_type=ChatMessageType.HUMAN, content=content, chat=session.chat)
    human.add_rating(rating)
    ChatMessageFactory.create(message_type=ChatMessageType.AI, content=f"{content} reply", chat=session.chat)


def add_good_and_bad_pairs(session):
    add_rated_pair(session, "good", "+1")
    add_rated_pair(session, "bad", "-1")


@pytest.mark.django_db()
def test_every_pair_is_yielded_across_chunk_boundaries(experiment_and_channel):
    """A chunk size that doesn't divide the session count must not drop or repeat pairs."""
    experiment, channel = experiment_and_channel
    make_sessions(5, experiment, channel)

    result = list(
        iter_message_evaluation_messages_for_sessions(
            ExperimentSession.objects.filter(experiment=experiment), chunk_size=2
        )
    )

    expected_input_ids = ChatMessage.objects.filter(
        message_type=ChatMessageType.HUMAN, chat__experiment_session__experiment=experiment
    ).values_list("id", flat=True)
    assert sorted(message.input_chat_message_id for message in result) == sorted(expected_input_ids)


@pytest.mark.django_db()
def test_each_chunk_builds_at_most_chunk_size_sessions(experiment_and_channel):
    """Each call to the pair builder receives one chunk of sessions, never the whole selection."""
    experiment, channel = experiment_and_channel
    make_sessions(5, experiment, channel)

    with patch(
        "apps.evaluations.utils.make_evaluation_messages_from_sessions", wraps=make_evaluation_messages_from_sessions
    ) as spy:
        list(
            iter_message_evaluation_messages_for_sessions(
                ExperimentSession.objects.filter(experiment=experiment), chunk_size=2
            )
        )

    assert spy.call_count == 3
    assert [len(call.args[0]) for call in spy.call_args_list] == [2, 2, 1]


@pytest.mark.django_db()
def test_default_chunk_size_is_patchable(experiment_and_channel):
    """The module constant is read at call time, so tests can shrink it."""
    experiment, channel = experiment_and_channel
    make_sessions(3, experiment, channel)

    with (
        patch("apps.evaluations.utils.SESSION_CHUNK_SIZE", 1),
        patch(
            "apps.evaluations.utils.make_evaluation_messages_from_sessions",
            wraps=make_evaluation_messages_from_sessions,
        ) as spy,
    ):
        list(iter_message_evaluation_messages_for_sessions(ExperimentSession.objects.filter(experiment=experiment)))

    assert spy.call_count == 3


@pytest.mark.django_db()
def test_message_filter_applies_within_each_chunk(experiment_and_channel):
    """With one session per chunk, the filter still keeps only the matching pair from each."""
    experiment, channel = experiment_and_channel
    for session in make_sessions(2, experiment, channel, messages_per_session=0):
        add_good_and_bad_pairs(session)

    result = list(
        iter_message_evaluation_messages_for_sessions(
            ExperimentSession.objects.filter(experiment=experiment), filter_params=POSITIVE_FILTER, chunk_size=1
        )
    )

    assert [message.input["content"] for message in result] == ["good", "good"]


@pytest.mark.django_db()
def test_chunks_carry_their_session_counts(experiment_and_channel):
    """Each chunk reports how many sessions it walked and holds only those sessions' pairs."""
    experiment, channel = experiment_and_channel
    sessions = make_sessions(5, experiment, channel)

    chunks = list(iter_message_evaluation_chunks(ExperimentSession.objects.filter(experiment=experiment), chunk_size=2))

    assert [session_count for session_count, _ in chunks] == [2, 2, 1]
    session_ids_per_chunk = [{message.session_id for message in messages} for _, messages in chunks]
    assert session_ids_per_chunk == [
        {sessions[0].id, sessions[1].id},
        {sessions[2].id, sessions[3].id},
        {sessions[4].id},
    ]


@pytest.mark.django_db()
def test_id_entry_point_combines_regular_and_filtered_sources(experiment_and_channel):
    """Regular sessions keep every pair, filtered sessions only the matching ones, and none is built twice."""
    experiment, channel = experiment_and_channel
    regular, filtered = make_sessions(2, experiment, channel, messages_per_session=0)
    add_rated_pair(regular, "regular one", "+1")
    add_rated_pair(regular, "regular two", "-1")
    add_good_and_bad_pairs(filtered)
    team = experiment.team

    result = list(
        iter_message_evaluation_messages(team, [regular.external_id], [filtered.external_id], POSITIVE_FILTER, None)
    )

    assert len(result) == 3
    assert list(iter_message_evaluation_messages(team, [], [filtered.external_id], None, None)) == []
    both = list(
        iter_message_evaluation_messages(team, [regular.external_id], [regular.external_id], POSITIVE_FILTER, None)
    )
    assert len(both) == 2


@pytest.mark.django_db()
def test_id_entry_point_ignores_sessions_from_other_teams(experiment_and_channel):
    """A session id from another team is never read, even when it is passed in."""
    experiment, channel = experiment_and_channel
    (own,) = make_sessions(1, experiment, channel)
    foreign = ExperimentSessionFactory.create()
    ChatMessageFactory.create(message_type=ChatMessageType.HUMAN, content="secret", chat=foreign.chat)
    ChatMessageFactory.create(message_type=ChatMessageType.AI, content="secret reply", chat=foreign.chat)

    result = list(iter_message_evaluation_messages(experiment.team, [own.external_id, foreign.external_id]))

    assert [message.session_id for message in result] == [own.id]


@pytest.mark.django_db()
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
def test_task_streams_and_reports_session_progress(experiment_and_channel):
    """Progress counts the sessions of each committed chunk."""
    experiment, channel = experiment_and_channel
    sessions = make_sessions(3, experiment, channel)
    external_ids = [session.external_id for session in sessions]
    dataset = EvaluationDataset.objects.create(
        team=experiment.team, name="progress", evaluation_mode=EvaluationMode.MESSAGE
    )

    with (
        patch("apps.evaluations.utils.SESSION_CHUNK_SIZE", 1),
        patch("apps.evaluations.tasks.ProgressRecorder") as recorder,
    ):
        create_dataset_from_session_messages_task.delay(
            dataset.id, experiment.team.id, external_ids, [], None, None
        ).get()

    messages = [call.args[2] for call in recorder.return_value.set_progress.call_args_list]
    assert messages == [
        "Starting clone...",
        "Cloned messages from 1 of 3 sessions",
        "Cloned messages from 2 of 3 sessions",
        "Cloned messages from 3 of 3 sessions",
        "Clone complete",
    ]
    assert dataset.messages.count() == 3


def commit_first_chunk_then_die():
    """Build an add_messages_stream stand-in that commits the whole first chunk, then raises on the next one."""
    real_add = EvaluationDataset.add_messages_stream
    calls = itertools.count()

    def add_messages_stream(self, messages, **kwargs):
        if next(calls):
            raise RuntimeError("worker died")
        return real_add(self, messages, **kwargs)

    return add_messages_stream


@pytest.mark.django_db()
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
def test_task_resumes_after_partial_failure(experiment_and_channel):
    """The failure copy counts sessions whose chunk committed before the crash, and the re-run skips committed pairs."""
    experiment, channel = experiment_and_channel
    sessions = make_sessions(4, experiment, channel, messages_per_session=4)
    external_ids = [session.external_id for session in sessions]
    dataset = EvaluationDataset.objects.create(
        team=experiment.team, name="partial", evaluation_mode=EvaluationMode.MESSAGE
    )

    with (
        patch("apps.evaluations.utils.SESSION_CHUNK_SIZE", 1),
        patch.object(EvaluationDataset, "add_messages_stream", commit_first_chunk_then_die()),
    ):
        create_dataset_from_session_messages_task.delay(
            dataset.id, experiment.team.id, external_ids, [], None, None
        ).get()

    dataset.refresh_from_db()
    assert dataset.status == DatasetCreationStatus.FAILED
    assert dataset.messages.count() == 2
    assert dataset.error_message == (
        "Failed after cloning messages from 1 of 4 session(s): worker died. "
        "Re-running will resume from where it stopped."
    )

    with patch("apps.evaluations.utils.SESSION_CHUNK_SIZE", 1):
        result = create_dataset_from_session_messages_task.delay(
            dataset.id, experiment.team.id, external_ids, [], None, None
        ).get()

    dataset.refresh_from_db()
    assert dataset.status == DatasetCreationStatus.COMPLETED
    assert (result["created_count"], result["duplicates_skipped"], dataset.messages.count()) == (6, 2, 8)


def stream_progress(dataset, sessions, batch_boundary=None):
    """Stream the sessions' pairs into the dataset two per batch and return the progress callback calls."""
    calls = []
    dataset.add_messages_stream(
        iter_message_evaluation_messages_for_sessions(
            ExperimentSession.objects.filter(pk__in=[s.pk for s in sessions])
        ),
        batch_size=2,
        batch_boundary=batch_boundary,
        progress_callback=lambda created, skipped: calls.append((created, skipped)),
    )
    return calls


@pytest.mark.django_db()
def test_add_messages_stream_batch_boundary_keeps_sessions_whole(experiment_and_channel):
    """With a session boundary, a batch grows past batch_size rather than split a session's pairs."""
    experiment, channel = experiment_and_channel
    sessions = make_sessions(2, experiment, channel, messages_per_session=6)
    dataset = EvaluationDataset.objects.create(
        team=experiment.team, name="boundary", evaluation_mode=EvaluationMode.MESSAGE
    )

    calls = stream_progress(dataset, sessions, batch_boundary=lambda message: message.session_id)

    assert calls == [(3, 0), (6, 0)]
    assert dataset.messages.count() == 6


@pytest.mark.django_db()
def test_add_messages_stream_without_boundary_batches_by_size(experiment_and_channel):
    """Without a boundary, batches close at batch_size even in the middle of a session."""
    experiment, channel = experiment_and_channel
    sessions = make_sessions(2, experiment, channel, messages_per_session=6)
    dataset = EvaluationDataset.objects.create(
        team=experiment.team, name="no boundary", evaluation_mode=EvaluationMode.MESSAGE
    )

    assert stream_progress(dataset, sessions) == [(2, 0), (4, 0), (6, 0)]


@pytest.mark.django_db()
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
def test_task_resume_after_failure_keeps_split_session_whole(experiment_and_channel):
    """A crash after the first commit never leaves a session half-cloned, so the filter re-run picks up the rest."""
    experiment, channel = experiment_and_channel
    session_a, session_b = make_sessions(2, experiment, channel, messages_per_session=0)
    for turn in range(3):
        add_rated_pair(session_a, f"a-{turn}", "+1")
    add_rated_pair(session_b, "b-0", "+1")
    dataset = EvaluationDataset.objects.create(
        team=experiment.team, name="split", evaluation_mode=EvaluationMode.MESSAGE
    )

    with (
        patch("apps.evaluations.utils.SESSION_CHUNK_SIZE", 1),
        patch.object(EvaluationDataset, "add_messages_stream", commit_first_chunk_then_die()),
    ):
        create_dataset_from_session_messages_task.delay(
            dataset.id, experiment.team.id, [], [], None, None, session_filter_query=""
        ).get()

    dataset.refresh_from_db()
    assert dataset.status == DatasetCreationStatus.FAILED
    assert sorted(message.input["content"] for message in dataset.messages.all()) == ["a-0", "a-1", "a-2"]
    assert dataset.error_message == (
        "Failed after cloning messages from 1 of 2 session(s): worker died. "
        "Re-running will resume from where it stopped."
    )

    result = create_dataset_from_session_messages_task.delay(
        dataset.id, experiment.team.id, [], [], None, None, session_filter_query=""
    ).get()

    dataset.refresh_from_db()
    assert dataset.status == DatasetCreationStatus.COMPLETED
    assert result["created_count"] == 1
    assert result["duplicates_skipped"] == 0
    assert dataset.messages.count() == 4
