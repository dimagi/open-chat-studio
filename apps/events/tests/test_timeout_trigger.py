from datetime import timedelta
from unittest import mock

import pytest
from django.test import RequestFactory, override_settings
from time_machine import travel

from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.events.const import TOTAL_FAILURES
from apps.events.models import (
    EventAction,
    EventActionType,
    EventLogStatusChoices,
    TimeoutTrigger,
)
from apps.events.tasks import enqueue_timed_out_events
from apps.events.views import _delete_event_view
from apps.experiments.models import SessionStatus
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import (
    ExperimentFactory,
    ExperimentSessionFactory,
)
from apps.utils.factories.team import TeamWithUsersFactory


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
    with travel("2024-04-02", tick=False) as frozen_time:
        timeout_trigger = TimeoutTrigger.objects.create(
            experiment=session.experiment,
            action=EventAction.objects.create(action_type=EventActionType.LOG),
            delay=10 * 60,  # 10 minutes
        )

        chat = Chat.objects.create(team=session.team)
        message = ChatMessage.objects.create(
            chat=chat,
            content="Hello",
            message_type=ChatMessageType.HUMAN,
        )
        message.save()
        session.chat = chat
        session.save()

        frozen_time.shift(delta=timedelta(minutes=15))
        timed_out_sessions = timeout_trigger.timed_out_sessions()
        assert len(timed_out_sessions) == 1
        assert timed_out_sessions[0] == session


@pytest.mark.django_db()
def test_non_timed_out_sessions(session):
    """A human chat message was sent more recently than the timeout"""
    with travel("2024-04-02", tick=False) as frozen_time:
        timeout_trigger = TimeoutTrigger.objects.create(
            experiment=session.experiment,
            action=EventAction.objects.create(action_type=EventActionType.LOG),
            delay=10,  # 10 seconds
        )

        chat = Chat.objects.create(team=session.team)
        ChatMessage.objects.create(
            chat=chat,
            content="Hello",
            message_type=ChatMessageType.HUMAN,
        )
        session.chat = chat
        session.save()

        frozen_time.shift(delta=timedelta(seconds=5))
        timed_out_sessions = timeout_trigger.timed_out_sessions()
        assert len(timed_out_sessions) == 0


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
@mock.patch("apps.events.tasks.fire_trigger.run")
@pytest.mark.django_db()
def test_timed_out_sessions_fired(mock_fire_trigger, session):
    """A human chat message was sent more recently than the timeout"""
    with travel("2024-04-02", tick=False) as frozen_time:
        timeout_trigger = TimeoutTrigger.objects.create(
            experiment=session.experiment,
            action=EventAction.objects.create(action_type=EventActionType.LOG),
            delay=10 * 60,  # 10 Minutes
        )

        chat = Chat.objects.create(team=session.team)
        message = ChatMessage.objects.create(
            chat=chat,
            content="Hello",
            message_type=ChatMessageType.HUMAN,
        )
        message.save()
        session.chat = chat
        session.save()

        experiment_version = session.experiment.create_new_version(make_default=True)
        trigger_version = experiment_version.timeout_triggers.first()

        frozen_time.shift(delta=timedelta(minutes=15))
        timed_out_sessions = timeout_trigger.timed_out_sessions()
        assert len(timed_out_sessions) == 1
        enqueue_timed_out_events()
        mock_fire_trigger.assert_called_with(trigger_version.id, session.id)


@pytest.mark.django_db()
def test_trigger_count_reached(session):
    with travel("2024-04-02", tick=False) as frozen_time:
        timeout_trigger = TimeoutTrigger.objects.create(
            experiment=session.experiment,
            action=EventAction.objects.create(action_type=EventActionType.LOG),
            total_num_triggers=2,
            delay=10 * 60,  # 10 minutes
        )

        chat = Chat.objects.create(team=session.team)
        message = ChatMessage.objects.create(
            chat=chat,
            content="Hello",
            message_type=ChatMessageType.HUMAN,
        )
        message.save()
        session.chat = chat
        session.save()
        frozen_time.shift(delta=timedelta(minutes=11))

        assert len(timeout_trigger.timed_out_sessions()) == 1

        # Suppose there's a failed attempt, then the timeout should still trigger
        timeout_trigger.event_logs.create(session=session, chat_message=message, status=EventLogStatusChoices.FAILURE)
        assert len(timeout_trigger.timed_out_sessions()) == 1

        # Suppose there's a success attempt, then the timeout should not trigger
        timeout_trigger.event_logs.create(session=session, chat_message=message, status=EventLogStatusChoices.SUCCESS)
        assert len(timeout_trigger.timed_out_sessions()) == 0

        # The timeout passes after the next message is sent
        message_2 = ChatMessage.objects.create(
            chat=chat,
            content="Hello",
            message_type=ChatMessageType.HUMAN,
        )
        message_2.save()
        frozen_time.shift(delta=timedelta(minutes=11))
        assert len(timeout_trigger.timed_out_sessions()) == 1

        # Sending another message before the next timeout will not trigger
        ChatMessage.objects.create(
            chat=chat,
            content="Hello",
            message_type=ChatMessageType.HUMAN,
        )
        frozen_time.shift(delta=timedelta(minutes=9))
        assert len(timeout_trigger.timed_out_sessions()) == 0


@pytest.mark.django_db()
def test_failure_count_reached(session):
    with travel("2024-04-02", tick=False) as frozen_time:
        timeout_trigger = TimeoutTrigger.objects.create(
            experiment=session.experiment,
            action=EventAction.objects.create(action_type=EventActionType.LOG),
            total_num_triggers=2,
            delay=10 * 60,  # 10 minutes
        )

        chat = Chat.objects.create(team=session.team)
        message = ChatMessage.objects.create(
            chat=chat,
            content="Hello",
            message_type=ChatMessageType.HUMAN,
        )
        message.save()
        session.chat = chat
        session.save()
        frozen_time.shift(delta=timedelta(minutes=11))
        assert len(timeout_trigger.timed_out_sessions()) == 1

        assert timeout_trigger._has_triggers_left(timeout_trigger, session, message) is True
        for _ in range(TOTAL_FAILURES):
            timeout_trigger.event_logs.create(
                session=session, chat_message=message, status=EventLogStatusChoices.FAILURE
            )

        assert timeout_trigger._has_triggers_left(timeout_trigger, session, message) is False
        assert len(timeout_trigger.timed_out_sessions()) == 0

        # The timeout passes after the next message is sent
        message_2 = ChatMessage.objects.create(
            chat=chat,
            content="Hello",
            message_type=ChatMessageType.HUMAN,
        )
        message_2.save()
        frozen_time.shift(delta=timedelta(minutes=11))
        assert len(timeout_trigger.timed_out_sessions()) == 1


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
    [
        (SessionStatus.SETUP, True),
        (SessionStatus.PENDING, True),
        (SessionStatus.PENDING_PRE_SURVEY, True),
        (SessionStatus.ACTIVE, True),
        (SessionStatus.PENDING_REVIEW, False),
        (SessionStatus.COMPLETE, False),
        (SessionStatus.UNKNOWN, False),
    ],
)
def test_not_triggered_for_complete_chats(status, matches, session):
    session.status = status
    session.save()
    with travel("2024-04-02", tick=False) as frozen_time:
        timeout_trigger = TimeoutTrigger.objects.create(
            experiment=session.experiment,
            action=EventAction.objects.create(action_type=EventActionType.LOG),
            delay=10 * 60,
        )

        chat = Chat.objects.create(team=session.team)
        message = ChatMessage.objects.create(
            chat=chat,
            content="Hello",
            message_type=ChatMessageType.HUMAN,
        )
        message.save()
        session.chat = chat
        session.save()

        frozen_time.shift(delta=timedelta(minutes=15))
        timed_out_sessions = timeout_trigger.timed_out_sessions()
        if matches:
            assert len(timed_out_sessions) == 1
            assert timed_out_sessions[0] == session
        else:
            assert len(timed_out_sessions) == 0


@pytest.mark.django_db()
def test_not_triggered_no_human_message(session):
    with travel("2024-04-02", tick=False) as frozen_time:
        timeout_trigger = TimeoutTrigger.objects.create(
            experiment=session.experiment,
            action=EventAction.objects.create(action_type=EventActionType.LOG),
            delay=10 * 60,
        )

        chat = Chat.objects.create(team=session.team)
        message = ChatMessage.objects.create(
            chat=chat,
            content="Hello",
            message_type=ChatMessageType.AI,
        )
        message.save()
        session.chat = chat
        session.save()

        frozen_time.shift(delta=timedelta(minutes=15))
        timed_out_sessions = timeout_trigger.timed_out_sessions()
        assert len(timed_out_sessions) == 0


@pytest.mark.django_db()
def test_delete():
    team = TeamWithUsersFactory()
    experiment = ExperimentFactory(team=team)
    timeout_trigger = TimeoutTrigger.objects.create(
        experiment=experiment,
        action=EventAction.objects.create(action_type=EventActionType.LOG),
        delay=10 * 60,
    )
    request = RequestFactory().get("/")
    _delete_event_view(
        trigger_type="timeout",
        request=request,
        team_slug=experiment.team.slug,
        experiment_id=experiment.id,
        trigger_id=timeout_trigger.id,
    )
    timeout_trigger.refresh_from_db()
    assert timeout_trigger.is_archived, "The timeout trigger should be archived"
