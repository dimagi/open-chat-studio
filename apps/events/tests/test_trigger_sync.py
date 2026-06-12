import pytest

from apps.events.models import EventActionType, StaticTriggerType
from apps.events.versioning import sync_triggers
from apps.utils.factories.events import EventActionFactory, StaticTriggerFactory, TimeoutTriggerFactory
from apps.utils.factories.experiment import ExperimentFactory


@pytest.mark.django_db()
class TestSyncTriggers:
    def test_copies_triggers_and_actions_to_target(self):
        source = ExperimentFactory()
        target = ExperimentFactory(team=source.team)
        static = StaticTriggerFactory(
            experiment=source,
            type=StaticTriggerType.CONVERSATION_END,
            action=EventActionFactory(action_type=EventActionType.LOG, params={"key": "value"}),
        )
        timeout = TimeoutTriggerFactory(experiment=source, delay=60, total_num_triggers=3)

        sync_triggers(source, target)

        new_static = target.static_triggers.get()
        assert new_static.type == static.type
        assert new_static.working_version_id == static.id
        assert new_static.action_id != static.action_id
        assert new_static.action.action_type == static.action.action_type
        assert new_static.action.params == static.action.params

        new_timeout = target.timeout_triggers.get()
        assert new_timeout.delay == timeout.delay
        assert new_timeout.total_num_triggers == timeout.total_num_triggers
        assert new_timeout.working_version_id == timeout.id
        assert new_timeout.action_id != timeout.action_id

    def test_archives_target_triggers_absent_from_source(self):
        source = ExperimentFactory()
        target = ExperimentFactory(team=source.team)
        stale_static = StaticTriggerFactory(experiment=target)
        stale_timeout = TimeoutTriggerFactory(experiment=target)
        source_static = StaticTriggerFactory(experiment=source)

        sync_triggers(source, target)

        stale_static.refresh_from_db()
        stale_timeout.refresh_from_db()
        assert stale_static.is_archived
        assert stale_timeout.is_archived
        assert [t.working_version_id for t in target.static_triggers.all()] == [source_static.id]
        assert not target.timeout_triggers.exists()

    def test_sync_with_no_triggers_is_a_noop(self):
        source = ExperimentFactory()
        target = ExperimentFactory(team=source.team)

        sync_triggers(source, target)

        assert not target.static_triggers.exists()
        assert not target.timeout_triggers.exists()
