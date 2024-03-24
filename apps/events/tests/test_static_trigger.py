from datetime import timedelta
from unittest import mock

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.events.models import (
    EventAction,
    EventActionType,
    StaticTrigger,
    StaticTriggerType,
    TimeoutTrigger,
)
from apps.utils.factories.experiment import (
    ExperimentSessionFactory,
)


@pytest.fixture()
def session():
    return ExperimentSessionFactory()


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
@mock.patch("apps.events.tasks.fire_static_trigger.run")
@pytest.mark.django_db()
def test_end_conversation_fires_event(mock_fire_trigger, session):
    static_trigger = StaticTrigger.objects.create(
        experiment=session.experiment,
        action=EventAction.objects.create(action_type=EventActionType.LOG),
        type=StaticTriggerType.CONVERSATION_END,
    )
    session.end()

    mock_fire_trigger.assert_called_with(static_trigger.id, session.id)


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
@pytest.mark.django_db()
def test_last_timeout_can_end_conversation(session):
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

    static_trigger = StaticTrigger.objects.create(
        experiment=session.experiment,
        action=EventAction.objects.create(action_type=EventActionType.END_CONVERSATION),
        type=StaticTriggerType.LAST_TIMEOUT,
    )

    timeout_trigger.fire(session)
    session.refresh_from_db()
    assert session.ended_at is None
    assert static_trigger.event_logs.count() == 0

    timeout_trigger.fire(session)
    session.refresh_from_db()
    assert session.ended_at is not None
    assert static_trigger.event_logs.count() == 1
