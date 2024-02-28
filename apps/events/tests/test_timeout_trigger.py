from datetime import datetime, timedelta
from unittest import mock

import pytest
from django.test import override_settings
from pytz import UTC

from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.events.models import (
    EventAction,
    EventActionType,
    TimeoutTrigger,
    TriggerStats,
)
from apps.events.tasks import enqueue_timed_out_events
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
    timeout_trigger = TimeoutTrigger.objects.create(
        experiment=experiment,
        action=EventAction.objects.create(action_type=EventActionType.LOG),
        delay=10 * 60,
    )
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
    timeout_trigger = TimeoutTrigger.objects.create(
        experiment=experiment,
        action=EventAction.objects.create(action_type=EventActionType.LOG),
        delay=10,
    )
    timed_out_sessions = timeout_trigger.timed_out_sessions()
    assert len(timed_out_sessions) == 0


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
@mock.patch("apps.events.tasks.fire_trigger.run")
@pytest.mark.django_db()
def test_timed_out_sessions_fired(mock_fire_trigger, session, experiment):
    """A human chat message was sent more recently than the timeout"""
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
    timeout_trigger = TimeoutTrigger.objects.create(
        experiment=experiment,
        delay=10 * 60,
        action=EventAction.objects.create(action_type=EventActionType.LOG),
    )
    timed_out_sessions = timeout_trigger.timed_out_sessions()
    assert len(timed_out_sessions) == 1
    enqueue_timed_out_events()
    mock_fire_trigger.assert_called_with(timeout_trigger.id, experiment.id)


def test_trigger_count_reached(session, experiment):
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
    timeout_trigger = TimeoutTrigger.objects.create(
        experiment=experiment,
        action=EventAction.objects.create(action_type=EventActionType.LOG),
        total_num_triggers=5,
        delay=10 * 60,
    )
    TriggerStats.objects.create(trigger=timeout_trigger, session=session, trigger_count=6)
    timed_out_sessions = timeout_trigger.timed_out_sessions()
    assert len(timed_out_sessions) == 0


def test_fire_trigger_increments_stats(session, experiment):
    chat = Chat.objects.create(team=session.team)
    ChatMessage.objects.create(
        chat=chat,
        content="Hello",
        message_type=ChatMessageType.HUMAN,
    )
    session.chat = chat
    session.save()
    timeout_trigger = TimeoutTrigger.objects.create(
        experiment=experiment,
        action=EventAction.objects.create(action_type=EventActionType.LOG),
        total_num_triggers=2,
        delay=10 * 60,
    )

    timeout_trigger.fire(session)
    session.refresh_from_db()

    assert timeout_trigger.stats.get(session=session).trigger_count == 1
    assert session.ended_at is None

    timeout_trigger.fire(session)
    session.refresh_from_db()
    assert timeout_trigger.stats.get(session=session).trigger_count == 2
    assert session.ended_at is not None
