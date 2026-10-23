"""Message-mode generators hold one chunk of sessions at a time and keep pairs, filter and counts correct."""

from unittest.mock import patch

import pytest

from apps.chat.models import ChatMessage, ChatMessageType
from apps.evaluations.tests.clone_helpers import make_sessions
from apps.evaluations.utils import (
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
def test_on_chunk_reports_sessions_per_chunk(experiment_and_channel):
    """The callback receives each chunk's session count, after that chunk's messages have been yielded."""
    experiment, channel = experiment_and_channel
    make_sessions(5, experiment, channel)
    yielded = 0
    reported = []

    stream = iter_message_evaluation_messages_for_sessions(
        ExperimentSession.objects.filter(experiment=experiment),
        chunk_size=2,
        on_chunk=lambda count: reported.append((count, yielded)),
    )
    for _ in stream:
        yielded += 1

    assert reported == [(2, 2), (2, 4), (1, 5)]


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
