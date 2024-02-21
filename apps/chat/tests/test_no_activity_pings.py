from datetime import timedelta

import pytest
from freezegun import freeze_time

from apps.chat.models import ChatMessage, ChatMessageType
from apps.chat.tasks import _get_sessions_to_ping
from apps.experiments.models import NoActivityMessageConfig, SessionStatus
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.team import TeamFactory


@pytest.fixture()
def team():
    return TeamFactory()


@pytest.fixture()
def no_activity_config(team):
    return NoActivityMessageConfig.objects.create(
        team=team, message_for_bot="Some message", name="Some name", max_pings=3, ping_after=1
    )


@pytest.fixture()
def experiment(no_activity_config):
    return ExperimentFactory(no_activity_config=no_activity_config, team=no_activity_config.team)


@pytest.fixture()
def session(experiment):
    return ExperimentSessionFactory(team=experiment.team, experiment=experiment)


@pytest.mark.django_db()
def test_returns_empty_list(session):
    sessions_to_ping = _get_sessions_to_ping()
    assert not sessions_to_ping


@pytest.mark.django_db()
def test_matches_session(session):
    with freeze_time("2022-01-01") as frozen_time:
        _create_matching_chat(session, frozen_time)
        frozen_time.tick(delta=timedelta(minutes=2))
        sessions_to_ping = _get_sessions_to_ping()
        assert sessions_to_ping == [session]


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("status", "matches"),
    {
        (SessionStatus.SETUP, True),
        (SessionStatus.PENDING, True),
        (SessionStatus.PENDING_PRE_SURVEY, True),
        (SessionStatus.ACTIVE, True),
        (SessionStatus.PENDING_REVIEW, False),
        (SessionStatus.COMPLETE, False),
        (SessionStatus.UNKNOWN, False),
    },
)
def test_status_filtering(status, matches, session):
    session.status = status
    session.save()
    with freeze_time("2022-01-01") as frozen_time:
        _create_matching_chat(session, frozen_time)
        frozen_time.tick(delta=timedelta(minutes=2))
        sessions_to_ping = _get_sessions_to_ping()
        assert sessions_to_ping == ([session] if matches else [])


@pytest.mark.django_db()
def test_filter_when_no_human_message(session):
    with freeze_time("2022-01-01") as frozen_time:
        ChatMessage.objects.create(chat=session.chat, message_type=ChatMessageType.AI, content="Hi!")

        frozen_time.tick(delta=timedelta(minutes=2))
        assert not _get_sessions_to_ping()


@pytest.mark.django_db()
def test_filter_when_last_message_was_human(session):
    with freeze_time("2022-01-01") as frozen_time:
        _create_chat(session, frozen_time)
        frozen_time.tick(delta=timedelta(minutes=2))
        assert not _get_sessions_to_ping()


@pytest.mark.django_db()
def test_filter_on_max_pings(session):
    session.no_activity_ping_count = 3
    session.save()
    with freeze_time("2022-01-01") as frozen_time:
        _create_matching_chat(session, frozen_time)
        frozen_time.tick(delta=timedelta(minutes=2))
        assert not _get_sessions_to_ping()


def _create_matching_chat(session, frozen_time):
    _create_chat(
        session,
        frozen_time,
        [
            ChatMessage(chat=session.chat, message_type=ChatMessageType.AI, content="How can I help?"),
        ],
    )


def _create_chat(session, frozen_time, messages=None):
    ChatMessage.objects.create(chat=session.chat, message_type=ChatMessageType.AI, content="Hi!")
    frozen_time.tick(1)
    ChatMessage.objects.create(chat=session.chat, message_type=ChatMessageType.HUMAN, content="Hello!")
    if messages:
        for message in messages:
            frozen_time.tick(1)
            message.save()
