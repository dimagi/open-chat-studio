import importlib

import pytest
from django.db import connection
from django.db.migrations.loader import MigrationLoader

from apps.events.models import EventAction, EventActionType, EventLogStatusChoices, StaticTrigger, StaticTriggerType
from apps.utils.factories.events import EventActionFactory, StaticTriggerFactory
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory

_MIGRATION_NAME = "0029_remove_end_conversation_end_triggers"
_migration = importlib.import_module(f"apps.events.migrations.{_MIGRATION_NAME}")


def _run():
    # Historical models have no GenericRelation, so this checks event logs are deleted explicitly.
    historical_apps = MigrationLoader(connection).project_state(("events", _MIGRATION_NAME)).apps
    _migration.remove_end_conversation_end_triggers(historical_apps, None)


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("trigger_type", "action_type", "is_removed"),
    [
        pytest.param(StaticTriggerType.CONVERSATION_END, EventActionType.END_CONVERSATION, True, id="end-any-means"),
        pytest.param(
            StaticTriggerType.CONVERSATION_ENDED_BY_USER, EventActionType.END_CONVERSATION, True, id="end-by-user"
        ),
        pytest.param(StaticTriggerType.CONVERSATION_END, EventActionType.LOG, False, id="end-with-log"),
        pytest.param(StaticTriggerType.LAST_TIMEOUT, EventActionType.END_CONVERSATION, False, id="last-timeout"),
        pytest.param(StaticTriggerType.NEW_HUMAN_MESSAGE, EventActionType.END_CONVERSATION, False, id="human-message"),
    ],
)
def test_removes_only_end_triggers_that_end_the_conversation(trigger_type, action_type, is_removed):
    trigger = StaticTriggerFactory.create(type=trigger_type, action=EventActionFactory.create(action_type=action_type))

    _run()

    assert StaticTrigger.objects.get_all().filter(id=trigger.id).exists() is not is_removed
    assert EventAction.objects.filter(id=trigger.action_id).exists() is not is_removed


@pytest.mark.django_db()
def test_removes_versions_archived_triggers_and_logs():
    experiment = ExperimentFactory.create()
    trigger = StaticTriggerFactory.create(
        experiment=experiment,
        type=StaticTriggerType.CONVERSATION_END,
        action=EventActionFactory.create(action_type=EventActionType.END_CONVERSATION),
    )
    version = trigger.create_new_version(new_experiment=ExperimentFactory.create(team=experiment.team))
    archived = StaticTriggerFactory.create(
        experiment=experiment,
        type=StaticTriggerType.CONVERSATION_ENDED_BY_EVENT,
        action=EventActionFactory.create(action_type=EventActionType.END_CONVERSATION),
        is_archived=True,
    )
    trigger.event_logs.create(
        session=ExperimentSessionFactory.create(experiment=experiment),
        status=EventLogStatusChoices.SUCCESS,
        log="Session ended",
    )
    removed = [trigger, version, archived]

    _run()

    assert not StaticTrigger.objects.get_all().filter(id__in=[t.id for t in removed]).exists()
    assert not EventAction.objects.filter(id__in=[t.action_id for t in removed]).exists()
    assert not trigger.event_logs.exists()
