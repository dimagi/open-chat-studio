import pytest

from apps.events.models import EventActionType, StaticTriggerType
from apps.events.versioning import TriggerSyncMode, sync_triggers
from apps.utils.factories.events import EventActionFactory, StaticTriggerFactory, TimeoutTriggerFactory
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.pipelines import PipelineFactory


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


def _pipeline_start_trigger(experiment, pipeline_id):
    return StaticTriggerFactory(
        experiment=experiment,
        type=StaticTriggerType.CONVERSATION_END,
        action=EventActionFactory(
            action_type=EventActionType.PIPELINE_START,
            params={"pipeline_id": pipeline_id, "input_type": "last_message"},
        ),
    )


@pytest.mark.django_db()
class TestEventActionParamRemap:
    def test_publish_pins_pipeline_param_to_a_version(self):
        source = ExperimentFactory()
        target = ExperimentFactory(team=source.team)
        pipeline = PipelineFactory(team=source.team)
        trigger = _pipeline_start_trigger(source, pipeline.id)

        sync_triggers(source, target, mode=TriggerSyncMode.PUBLISH)

        pinned_id = target.static_triggers.get().action.params["pipeline_id"]
        pipeline.refresh_from_db()
        assert pinned_id == pipeline.latest_version.id
        assert pinned_id != pipeline.id
        # The source (working) trigger keeps pointing at the working pipeline.
        assert trigger.action.params["pipeline_id"] == pipeline.id

    def test_revert_maps_pipeline_param_back_to_working(self):
        source = ExperimentFactory()
        target = ExperimentFactory(team=source.team)
        pipeline = PipelineFactory(team=source.team)
        pipeline_version = pipeline.create_new_version()
        _pipeline_start_trigger(source, pipeline_version.id)

        sync_triggers(source, target, mode=TriggerSyncMode.REVERT)

        new_trigger = target.static_triggers.get()
        assert new_trigger.action.params["pipeline_id"] == pipeline.id
        assert new_trigger.working_version_id is None

    def test_revert_clears_dangling_pipeline_reference(self):
        source = ExperimentFactory()
        target = ExperimentFactory(team=source.team)
        _pipeline_start_trigger(source, 999999)

        sync_triggers(source, target, mode=TriggerSyncMode.REVERT)

        assert target.static_triggers.get().action.params["pipeline_id"] is None

    def test_copy_keeps_pipeline_param_verbatim(self):
        source = ExperimentFactory()
        target = ExperimentFactory(team=source.team)
        pipeline = PipelineFactory(team=source.team)
        _pipeline_start_trigger(source, pipeline.id)

        sync_triggers(source, target, mode=TriggerSyncMode.COPY)

        pipeline.refresh_from_db()
        assert target.static_triggers.get().action.params["pipeline_id"] == pipeline.id
        assert pipeline.latest_version is None
