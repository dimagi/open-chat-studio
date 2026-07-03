from datetime import timedelta
from unittest import mock

import pytest
from django.utils import timezone
from time_machine import travel

from apps.chat.models import Chat, ChatMessage, ChatMessageType
from apps.events.models import (
    EventAction,
    EventActionType,
    ScheduledMessage,
    StaticTriggerType,
    TimeoutTrigger,
)
from apps.events.tasks import enqueue_static_triggers, enqueue_timed_out_events, poll_scheduled_messages
from apps.teams.export_service import migrating_team_ids
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.events import (
    EventActionFactory,
    StaticTriggerFactory,
)
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory


@pytest.fixture()
def session():
    experiment = ExperimentFactory()
    channel = ExperimentChannelFactory(experiment=experiment, platform="web")
    return ExperimentSessionFactory(experiment=experiment, experiment_channel=channel)


def _arm_migration_lock(team, armed=True):
    team.is_migrating = armed
    team.save()


@pytest.mark.django_db()
def test_migrating_team_ids_lists_only_armed_teams():
    """Only teams with is_migrating=True appear in migrating_team_ids()."""
    session = ExperimentSessionFactory()
    team = session.team
    assert team.id not in set(migrating_team_ids())
    _arm_migration_lock(team)
    assert team.id in set(migrating_team_ids())


@pytest.mark.django_db()
def test_poll_scheduled_messages_skips_migrating_team(session):
    """poll_scheduled_messages doesn't trigger due messages while the team is migrating."""
    ScheduledMessage.objects.create(
        team=session.team,
        experiment=session.experiment,
        participant=session.participant,
        action=EventActionFactory(params={"name": "Test"}),
        next_trigger_date=timezone.now(),
    )

    _arm_migration_lock(session.team)
    with mock.patch.object(ScheduledMessage, "safe_trigger") as mock_trigger:
        poll_scheduled_messages()
    mock_trigger.assert_not_called()

    _arm_migration_lock(session.team, armed=False)
    with mock.patch.object(ScheduledMessage, "safe_trigger") as mock_trigger:
        poll_scheduled_messages()
    mock_trigger.assert_called_once()


@pytest.mark.django_db()
def test_enqueue_static_triggers_skips_migrating_team(session):
    """enqueue_static_triggers doesn't fire triggers while the team is migrating."""
    StaticTriggerFactory(
        experiment=session.experiment_version, type=StaticTriggerType.CONVERSATION_START, is_active=True
    )

    _arm_migration_lock(session.team)
    with mock.patch("apps.events.tasks.fire_static_trigger.delay") as mock_fire:
        enqueue_static_triggers(session.id, StaticTriggerType.CONVERSATION_START)
    mock_fire.assert_not_called()

    _arm_migration_lock(session.team, armed=False)
    with mock.patch("apps.events.tasks.fire_static_trigger.delay") as mock_fire:
        enqueue_static_triggers(session.id, StaticTriggerType.CONVERSATION_START)
    mock_fire.assert_called_once()


@pytest.mark.django_db()
def test_enqueue_timed_out_events_skips_migrating_team(session):
    """enqueue_timed_out_events doesn't fire timeout triggers while the team is migrating."""
    with travel("2024-04-02", tick=False) as frozen_time:
        TimeoutTrigger.objects.create(
            experiment=session.experiment,
            action=EventAction.objects.create(action_type=EventActionType.LOG),
            delay=10 * 60,
        )
        chat = Chat.objects.create(team=session.team)
        ChatMessage.objects.create(chat=chat, content="Hello", message_type=ChatMessageType.HUMAN)
        session.chat = chat
        session.save()
        session.experiment.create_new_version(make_default=True)
        frozen_time.shift(delta=timedelta(minutes=15))

        _arm_migration_lock(session.team)
        with mock.patch("apps.events.tasks.fire_trigger.delay") as mock_fire:
            enqueue_timed_out_events()
        mock_fire.assert_not_called()

        _arm_migration_lock(session.team, armed=False)
        with mock.patch("apps.events.tasks.fire_trigger.delay") as mock_fire:
            enqueue_timed_out_events()
        mock_fire.assert_called_once()
