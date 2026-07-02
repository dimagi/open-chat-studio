import pytest
from django.utils import timezone

from apps.events.models import ScheduledMessage, StaticTriggerType
from apps.events.tasks import _get_static_triggers_to_fire
from apps.teams.export_service import migrating_team_ids
from apps.utils.factories.events import (
    EventActionFactory,
    StaticTriggerFactory,
)
from apps.utils.factories.experiment import ExperimentSessionFactory


@pytest.mark.django_db()
def test_migrating_team_ids_lists_only_armed_teams():
    """Only teams with is_migrating=True appear in migrating_team_ids()."""
    session = ExperimentSessionFactory()
    team = session.team
    assert team.id not in set(migrating_team_ids())
    team.is_migrating = True
    team.save()
    assert team.id in set(migrating_team_ids())


@pytest.mark.django_db()
def test_due_scheduled_message_excluded_when_team_migrating():
    """Due scheduled messages and static triggers are excluded from firing while the team is migrating."""
    session = ExperimentSessionFactory()
    team = session.team
    message = ScheduledMessage.objects.create(
        team=team,
        experiment=session.experiment,
        participant=session.participant,
        action=EventActionFactory(params={"name": "Test"}),
        next_trigger_date=timezone.now(),
    )
    trigger = StaticTriggerFactory(
        experiment=session.experiment_version, type=StaticTriggerType.CONVERSATION_START, is_active=True
    )

    to_fire = ScheduledMessage.objects.get_messages_to_fire().exclude(team_id__in=migrating_team_ids())
    assert message in to_fire
    trigger_ids = list(_get_static_triggers_to_fire(session.id, StaticTriggerType.CONVERSATION_START))
    assert trigger.id in trigger_ids

    team.is_migrating = True
    team.save()

    to_fire = ScheduledMessage.objects.get_messages_to_fire().exclude(team_id__in=migrating_team_ids())
    assert message not in to_fire
    trigger_ids = list(_get_static_triggers_to_fire(session.id, StaticTriggerType.CONVERSATION_START))
    assert trigger.id not in trigger_ids
