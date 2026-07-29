"""Bounded-memory contract for session-mode dataset population (issue #3963).

These tests pin the *streaming* behaviour rather than the output: sessions are read in
keyset-paginated chunks, query count tracks chunk count rather than session count, and a
partially-completed run resumes instead of duplicating work.

Following apps/experiments/tests/test_export.py, these shrink the chunk size rather than
growing the data — ExperimentSessionFactory builds an Experiment, Chat, Participant and
ExperimentChannel per call, so asserting on 10k sessions directly is not viable.
"""

import itertools
from unittest.mock import patch

import pytest
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from apps.chat.models import ChatMessage, ChatMessageType
from apps.evaluations.models import DatasetCreationStatus, EvaluationDataset, EvaluationMode
from apps.evaluations.tasks import create_dataset_from_sessions_task
from apps.evaluations.utils import (
    iter_session_evaluation_messages,
    iter_session_evaluation_messages_for_sessions,
)
from apps.experiments.models import ExperimentSession
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory

# Resolving the selected external ids to primary keys, done once up front.
PK_RESOLUTION_QUERIES = 1
# One chunk costs: the chunk's sessions, their messages, and their traces.
QUERIES_PER_CHUNK = 3


def _make_sessions(count, experiment, channel, messages_per_session=2):
    """Create `count` sessions sharing one experiment/channel, each with a short conversation."""
    sessions = []
    chat_messages = []
    for session_index in range(count):
        session = ExperimentSessionFactory.create(experiment=experiment, experiment_channel=channel)
        for turn in range(messages_per_session):
            chat_messages.append(
                ChatMessage(
                    chat=session.chat,
                    message_type=ChatMessageType.HUMAN if turn % 2 == 0 else ChatMessageType.AI,
                    content=f"session-{session_index} turn-{turn}",
                )
            )
        sessions.append(session)
    ChatMessage.objects.bulk_create(chat_messages)
    return sessions


@pytest.fixture()
def experiment_and_channel():
    experiment = ExperimentFactory.create()
    return experiment, ExperimentChannelFactory.create(team=experiment.team)


@pytest.mark.django_db()
def test_every_session_is_yielded_across_chunk_boundaries(experiment_and_channel):
    """A chunk size that doesn't divide the session count must not drop or repeat sessions."""
    experiment, channel = experiment_and_channel
    sessions = _make_sessions(5, experiment, channel)

    result = list(
        iter_session_evaluation_messages([session.external_id for session in sessions], chunk_size=2),
    )

    assert [message.session_id for message in result] == sorted(session.id for session in sessions)
    assert all(len(message.history) == 2 for message in result)


@pytest.mark.django_db()
def test_default_chunk_size_is_patchable(experiment_and_channel):
    """The module constant is read at call time, so tests can shrink it (as test_export.py does)."""
    experiment, channel = experiment_and_channel
    sessions = _make_sessions(3, experiment, channel)
    external_ids = [session.external_id for session in sessions]

    with patch("apps.evaluations.utils.SESSION_CHUNK_SIZE", 1), CaptureQueriesContext(connection) as ctx:
        result = list(iter_session_evaluation_messages(external_ids))

    assert len(result) == 3
    # Chunks of 1 over 3 sessions, on top of the single pk-resolution query.
    assert len(ctx.captured_queries) == PK_RESOLUTION_QUERIES + QUERIES_PER_CHUNK * 3


@pytest.mark.django_db()
def test_query_count_is_independent_of_session_count(experiment_and_channel):
    """Doubling the sessions inside one chunk must not change the number of queries.

    This is the regression guard for the original bug: the old implementation issued a fixed
    number of queries but loaded every message into memory. Chunking trades that for a query
    count driven by chunk count, so the per-chunk cost must stay flat as sessions grow.
    """
    experiment, channel = experiment_and_channel
    sessions = _make_sessions(2, experiment, channel)

    with CaptureQueriesContext(connection) as ctx_small:
        list(iter_session_evaluation_messages([s.external_id for s in sessions], chunk_size=50))

    sessions += _make_sessions(2, experiment, channel)

    with CaptureQueriesContext(connection) as ctx_large:
        list(iter_session_evaluation_messages([s.external_id for s in sessions], chunk_size=50))

    assert (
        len(ctx_small.captured_queries) == len(ctx_large.captured_queries) == PK_RESOLUTION_QUERIES + QUERIES_PER_CHUNK
    )


@pytest.mark.django_db()
def test_sessions_without_messages_are_skipped(experiment_and_channel):
    """An empty session mid-chunk is skipped without disturbing its neighbours."""
    experiment, channel = experiment_and_channel
    sessions = _make_sessions(2, experiment, channel)
    empty = ExperimentSessionFactory.create(experiment=experiment, experiment_channel=channel)

    result = list(
        iter_session_evaluation_messages(
            [sessions[0].external_id, empty.external_id, sessions[1].external_id], chunk_size=2
        )
    )

    assert [message.session_id for message in result] == sorted(session.id for session in sessions)


@pytest.mark.django_db()
def test_add_messages_stream_commits_each_batch(experiment_and_channel):
    """Progress is reported per batch, and every message lands exactly once."""
    experiment, channel = experiment_and_channel
    sessions = _make_sessions(5, experiment, channel)
    dataset = EvaluationDataset.objects.create(
        team=experiment.team, name="streamed", evaluation_mode=EvaluationMode.SESSION
    )
    progress = []

    created_ids, skipped = dataset.add_messages_stream(
        iter_session_evaluation_messages([s.external_id for s in sessions], chunk_size=2),
        batch_size=2,
        progress_callback=lambda created, skipped_count: progress.append((created, skipped_count)),
    )

    assert len(created_ids) == 5
    assert skipped == 0
    # Batches of 2 over 5 messages: 2, 4, then the short final batch.
    assert progress == [(2, 0), (4, 0), (5, 0)]
    assert dataset.messages.count() == 5


@pytest.mark.django_db()
def test_add_messages_stream_is_idempotent(experiment_and_channel):
    """Re-streaming the same sessions skips them all, so a failed run can simply be retried."""
    experiment, channel = experiment_and_channel
    sessions = _make_sessions(4, experiment, channel)
    external_ids = [session.external_id for session in sessions]
    dataset = EvaluationDataset.objects.create(
        team=experiment.team, name="streamed", evaluation_mode=EvaluationMode.SESSION
    )

    dataset.add_messages_stream(iter_session_evaluation_messages(external_ids, chunk_size=2), batch_size=2)
    created_ids, skipped = dataset.add_messages_stream(
        iter_session_evaluation_messages(external_ids, chunk_size=2), batch_size=2
    )

    assert created_ids == []
    assert skipped == 4
    assert dataset.messages.count() == 4


@pytest.mark.django_db()
@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
def test_task_resumes_after_partial_failure(experiment_and_channel):
    """A run that dies mid-stream leaves a FAILED dataset that a re-run completes."""
    experiment, channel = experiment_and_channel
    sessions = _make_sessions(4, experiment, channel)
    external_ids = [session.external_id for session in sessions]
    dataset = EvaluationDataset.objects.create(
        team=experiment.team, name="partial", evaluation_mode=EvaluationMode.SESSION
    )

    real_add = EvaluationDataset.add_messages_stream

    def die_halfway(self, messages, *, batch_size=None, progress_callback=None):
        """Commit the first two sessions, then blow up as an OOM-killed worker would."""
        real_add(self, itertools.islice(messages, 2), batch_size=2, progress_callback=progress_callback)
        raise RuntimeError("worker died")

    with patch.object(EvaluationDataset, "add_messages_stream", die_halfway):
        create_dataset_from_sessions_task.delay(dataset.id, experiment.team.id, external_ids).get()

    dataset.refresh_from_db()
    assert dataset.status == DatasetCreationStatus.FAILED
    assert dataset.messages.count() == 2
    # The error names how far it got, so a human knows a retry will pick up the rest.
    assert "2 of 4 session(s)" in dataset.error_message

    result = create_dataset_from_sessions_task.delay(dataset.id, experiment.team.id, external_ids).get()

    dataset.refresh_from_db()
    assert dataset.status == DatasetCreationStatus.COMPLETED
    assert result["created_count"] == 2
    assert result["duplicates_skipped"] == 2
    assert dataset.messages.count() == 4


@pytest.mark.django_db()
def test_queryset_entry_point_pages_in_chunks(experiment_and_channel):
    """The filter-resolved entry point pages exactly like the id-list one.

    This is the path a "all sessions matching these filters" job takes: the task hands over a
    queryset, so no caller ever materialises the ids.
    """
    experiment, channel = experiment_and_channel
    sessions = _make_sessions(5, experiment, channel)

    queryset = ExperimentSession.objects.filter(experiment=experiment)
    with CaptureQueriesContext(connection) as ctx:
        result = list(iter_session_evaluation_messages_for_sessions(queryset, chunk_size=2))

    assert [message.session_id for message in result] == sorted(session.id for session in sessions)
    # 5 sessions in chunks of 2 is 3 chunks, on top of the single pk-resolution query.
    assert len(ctx.captured_queries) == PK_RESOLUTION_QUERIES + QUERIES_PER_CHUNK * 3
