from datetime import timedelta
from unittest import mock

import pytest
from django.test import override_settings
from freezegun import freeze_time

from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.events.models import (
    EventAction,
    EventActionType,
    EventLogStatusChoices,
    TimeoutTrigger,
)
from apps.events.tasks import enqueue_timed_out_events
from apps.experiments.models import SessionStatus
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import (
    ExperimentFactory,
    ExperimentSessionFactory,
)


@pytest.fixture()
def experiment():
    return ExperimentFactory()


@pytest.fixture()
def channel(experiment):
    return ExperimentChannelFactory(experiment=experiment, platform="web")


@pytest.fixture()
def session(experiment, channel):
    return ExperimentSessionFactory(experiment=experiment, experiment_channel=channel)


@pytest.mark.django_db()
def test_timed_out_sessions(session):
    """A human chat message was sent longer ago than the timeout"""
    timeout_trigger = TimeoutTrigger.objects.create(
        experiment=session.experiment,
        action=EventAction.objects.create(action_type=EventActionType.LOG),
        delay=10 * 60,  # 10 minutes
    )

    with freeze_time("2024-04-02") as frozen_time:
        chat = Chat.objects.create(team=session.team)
        message = ChatMessage.objects.create(
            chat=chat,
            content="Hello",
            message_type=ChatMessageType.HUMAN,
        )
        message.save()
        session.chat = chat
        session.save()

        frozen_time.tick(delta=timedelta(minutes=15))
        timed_out_sessions = timeout_trigger.timed_out_sessions()
        assert len(timed_out_sessions) == 1
        assert timed_out_sessions[0] == session


@pytest.mark.django_db()
def test_non_timed_out_sessions(session):
    """A human chat message was sent more recently than the timeout"""
    timeout_trigger = TimeoutTrigger.objects.create(
        experiment=session.experiment,
        action=EventAction.objects.create(action_type=EventActionType.LOG),
        delay=10,  # 10 seconds
    )
    with freeze_time("2024-04-02") as frozen_time:
        chat = Chat.objects.create(team=session.team)
        ChatMessage.objects.create(
            chat=chat,
            content="Hello",
            message_type=ChatMessageType.HUMAN,
        )
        session.chat = chat
        session.save()

        frozen_time.tick(delta=timedelta(seconds=5))
        timed_out_sessions = timeout_trigger.timed_out_sessions()
        assert len(timed_out_sessions) == 0


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
@mock.patch("apps.events.tasks.fire_trigger.run")
@pytest.mark.django_db()
def test_timed_out_sessions_fired(mock_fire_trigger, session):
    """A human chat message was sent more recently than the timeout"""
    timeout_trigger = TimeoutTrigger.objects.create(
        experiment=session.experiment,
        action=EventAction.objects.create(action_type=EventActionType.LOG),
        delay=10 * 60,  # 10 Minutes
    )

    with freeze_time("2024-04-02") as frozen_time:
        chat = Chat.objects.create(team=session.team)
        message = ChatMessage.objects.create(
            chat=chat,
            content="Hello",
            message_type=ChatMessageType.HUMAN,
        )
        message.save()
        session.chat = chat
        session.save()

        frozen_time.tick(delta=timedelta(minutes=15))
        timed_out_sessions = timeout_trigger.timed_out_sessions()
        assert len(timed_out_sessions) == 1
        enqueue_timed_out_events()
        mock_fire_trigger.assert_called_with(timeout_trigger.id, session.id)


@pytest.mark.django_db()
def test_trigger_count_reached(session):
    timeout_trigger = TimeoutTrigger.objects.create(
        experiment=session.experiment,
        action=EventAction.objects.create(action_type=EventActionType.LOG),
        total_num_triggers=2,
        delay=10 * 60,  # 10 minutes
    )
    with freeze_time("2024-04-02") as frozen_time:
        chat = Chat.objects.create(team=session.team)
        message = ChatMessage.objects.create(
            chat=chat,
            content="Hello",
            message_type=ChatMessageType.HUMAN,
        )
        message.save()
        session.chat = chat
        session.save()
        frozen_time.tick(delta=timedelta(minutes=11))

        timeout_trigger.event_logs.create(session=session, chat_message=message, status=EventLogStatusChoices.SUCCESS)
        assert len(timeout_trigger.timed_out_sessions()) == 1
        timeout_trigger.event_logs.create(session=session, chat_message=message, status=EventLogStatusChoices.FAILURE)
        assert len(timeout_trigger.timed_out_sessions()) == 1
        timeout_trigger.event_logs.create(session=session, chat_message=message, status=EventLogStatusChoices.SUCCESS)
        assert len(timeout_trigger.timed_out_sessions()) == 0

        # The timeout passes after the next message is sent
        message_2 = ChatMessage.objects.create(
            chat=chat,
            content="Hello",
            message_type=ChatMessageType.HUMAN,
        )
        message_2.save()
        frozen_time.tick(delta=timedelta(minutes=11))
        assert len(timeout_trigger.timed_out_sessions()) == 1

        # Sending another message before the next timeout will not trigger
        ChatMessage.objects.create(
            chat=chat,
            content="Hello",
            message_type=ChatMessageType.HUMAN,
        )
        frozen_time.tick(delta=timedelta(minutes=9))
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

    timeout_trigger.fire(session)
    session.refresh_from_db()
    assert timeout_trigger.event_logs.filter(session=session).count() == 2


@pytest.mark.django_db()
def test_new_human_message_resets_count(session):
    chat = Chat.objects.create(team=session.team)
    first_message = ChatMessage.objects.create(
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

    second_message = ChatMessage.objects.create(
        chat=chat,
        content="I'm still here!",
        message_type=ChatMessageType.HUMAN,
    )

    timeout_trigger.fire(session)
    session.refresh_from_db()
    assert timeout_trigger.event_logs.filter(session=session).count() == 2
    assert timeout_trigger.event_logs.filter(session=session, chat_message=first_message).count() == 1
    assert timeout_trigger.event_logs.filter(session=session, chat_message=second_message).count() == 1

    timeout_trigger.fire(session)
    session.refresh_from_db()
    assert timeout_trigger.event_logs.filter(session=session, chat_message=first_message).count() == 1
    assert timeout_trigger.event_logs.filter(session=session, chat_message=second_message).count() == 2


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
def test_not_triggered_complete_chats(status, matches, session):
    session.status = status
    session.save()
    timeout_trigger = TimeoutTrigger.objects.create(
        experiment=session.experiment,
        action=EventAction.objects.create(action_type=EventActionType.LOG),
        delay=10 * 60,
    )
    with freeze_time("2024-04-02") as frozen_time:
        chat = Chat.objects.create(team=session.team)
        message = ChatMessage.objects.create(
            chat=chat,
            content="Hello",
            message_type=ChatMessageType.HUMAN,
        )
        message.save()
        session.chat = chat
        session.save()

        frozen_time.tick(delta=timedelta(minutes=15))
        timed_out_sessions = timeout_trigger.timed_out_sessions()
        if matches:
            assert len(timed_out_sessions) == 1
            assert timed_out_sessions[0] == session
        else:
            assert len(timed_out_sessions) == 0


@pytest.mark.django_db()
def test_not_triggered_no_human_message(session):
    timeout_trigger = TimeoutTrigger.objects.create(
        experiment=session.experiment,
        action=EventAction.objects.create(action_type=EventActionType.LOG),
        delay=10 * 60,
    )
    with freeze_time("2024-04-02") as frozen_time:
        chat = Chat.objects.create(team=session.team)
        message = ChatMessage.objects.create(
            chat=chat,
            content="Hello",
            message_type=ChatMessageType.AI,
        )
        message.save()
        session.chat = chat
        session.save()

        frozen_time.tick(delta=timedelta(minutes=15))
        timed_out_sessions = timeout_trigger.timed_out_sessions()
        assert len(timed_out_sessions) == 0
