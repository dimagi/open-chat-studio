from datetime import datetime, timedelta

import pytest
from pytz import UTC

from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.events.models import TimeoutTrigger
from apps.utils.factories.experiment import (
    ExperimentFactory,
    ExperimentSessionFactory,
)


@pytest.fixture()
def experiment(team_with_users):
    return ExperimentFactory(team=team_with_users)


@pytest.fixture()
def session(experiment):
    return ExperimentSessionFactory(team=experiment.team, experiment=experiment)


@pytest.mark.django_db()
def test_timed_out_sessions(session, experiment):
    """A human chat message was sent longer ago than the timeout"""
    now = datetime.now().astimezone(UTC)
    fifteen_minutes_ago = now - timedelta(minutes=15)
    chat = Chat.objects.create(team=session.team)
    message = ChatMessage.objects.create(
        chat=chat,
        content="Hello",
        message_type=ChatMessageType.HUMAN,
    )
    message.created_at = fifteen_minutes_ago
    message.save()
    session.chat = chat
    session.save()
    timeout_trigger = TimeoutTrigger(experiment=experiment, delay=10 * 60)
    timed_out_sessions = timeout_trigger.timed_out_sessions()
    assert len(timed_out_sessions) == 1
    assert timed_out_sessions[0] == session


@pytest.mark.django_db()
def test_non_timed_out_sessions(session, experiment):
    """A human chat message was sent more recently than the timeout"""
    chat = Chat.objects.create(team=session.team)
    ChatMessage.objects.create(
        chat=chat,
        content="Hello",
        message_type=ChatMessageType.HUMAN,
    )
    session.chat = chat
    session.save()
    timeout_trigger = TimeoutTrigger(experiment=experiment, delay=10)
    timed_out_sessions = timeout_trigger.timed_out_sessions()
    assert len(timed_out_sessions) == 0
