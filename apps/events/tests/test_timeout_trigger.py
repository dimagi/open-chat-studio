from datetime import timedelta
from unittest import mock

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.events.models import (
    EventAction,
    EventActionType,
    EventLogStatusChoices,
    TimeoutTrigger,
)
from apps.events.tasks import enqueue_timed_out_events
from apps.utils.factories.experiment import (
    ExperimentSessionFactory,
)


@pytest.fixture()
def session():
    return ExperimentSessionFactory()


@pytest.mark.django_db()
def test_timed_out_sessions(session):
    """A human chat message was sent longer ago than the timeout"""
    fifteen_minutes_ago = timezone.now() - timedelta(minutes=15)
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
        experiment=session.experiment,
        action=EventAction.objects.create(action_type=EventActionType.LOG),
        delay=10 * 60,
    )
    timed_out_sessions = timeout_trigger.timed_out_sessions()
    assert len(timed_out_sessions) == 1
    assert timed_out_sessions[0] == session


@pytest.mark.django_db()
def test_non_timed_out_sessions(session):
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
        experiment=session.experiment,
        action=EventAction.objects.create(action_type=EventActionType.LOG),
        delay=10,
    )
    timed_out_sessions = timeout_trigger.timed_out_sessions()
    assert len(timed_out_sessions) == 0


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
@mock.patch("apps.events.tasks.fire_trigger.run")
@pytest.mark.django_db()
def test_timed_out_sessions_fired(mock_fire_trigger, session):
    """A human chat message was sent more recently than the timeout"""
    fifteen_minutes_ago = timezone.now() - timedelta(minutes=15)
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
        experiment=session.experiment,
        delay=10 * 60,
        action=EventAction.objects.create(action_type=EventActionType.LOG),
    )
    timed_out_sessions = timeout_trigger.timed_out_sessions()
    assert len(timed_out_sessions) == 1
    enqueue_timed_out_events()
    mock_fire_trigger.assert_called_with(timeout_trigger.id, session.id)


@pytest.mark.django_db()
def test_trigger_count_reached(session):
    fifteen_minutes_ago = timezone.now() - timedelta(minutes=15)
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
        experiment=session.experiment,
        action=EventAction.objects.create(action_type=EventActionType.LOG),
        total_num_triggers=2,
        delay=10 * 60,
    )
    timeout_trigger.add_event_log(session, EventLogStatusChoices.SUCCESS)
    assert len(timeout_trigger.timed_out_sessions()) == 1
    timeout_trigger.add_event_log(session, EventLogStatusChoices.FAILURE)
    assert len(timeout_trigger.timed_out_sessions()) == 1
    timeout_trigger.add_event_log(session, EventLogStatusChoices.SUCCESS)
    assert len(timeout_trigger.timed_out_sessions()) == 0


@pytest.mark.django_db()
def test_fire_trigger_increments_stats(session):
    chat = Chat.objects.create(team=session.team)
    ChatMessage.objects.create(
        chat=chat,
        content="Hello",
        message_type=ChatMessageType.HUMAN,
    )
    session.chat = chat
    session.save()
    timeout_trigger = TimeoutTrigger.objects.create(
        experiment=session.experiment,
        action=EventAction.objects.create(action_type=EventActionType.LOG),
        total_num_triggers=2,
        delay=10 * 60,
    )

    timeout_trigger.fire(session)
    session.refresh_from_db()

    assert timeout_trigger.event_logs.filter(session=session).count() == 1
    assert session.ended_at is None

    timeout_trigger.fire(session)
    session.refresh_from_db()
    assert timeout_trigger.event_logs.filter(session=session).count() == 2
    assert session.ended_at is not None


@pytest.mark.django_db()
def test_new_human_message_resets_count(session):
    pass
