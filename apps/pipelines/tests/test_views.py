import pytest

from apps.events.models import EventActionType, StaticTriggerType
from apps.pipelines.views import get_widget_page_context
from apps.utils.factories.events import EventActionFactory, StaticTriggerFactory, TimeoutTriggerFactory
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.pipelines import PipelineFactory


@pytest.mark.django_db()
class TestGetWidgetPageContext:
    def test_returns_empty_dict_when_pipeline_is_none(self):
        assert get_widget_page_context(None) == {}

    def test_strips_node_positions(self):
        pipeline = PipelineFactory(
            data={
                "nodes": [
                    {
                        "id": "1",
                        "type": "pipelineNode",
                        "position": {"x": 100, "y": 200},
                        "data": {"id": "1", "type": "LLMResponseNode", "label": "Node 1"},
                    },
                    {
                        "id": "2",
                        "type": "pipelineNode",
                        "position": {"x": 300, "y": 400},
                        "data": {"id": "2", "type": "LLMResponseNode", "label": "Node 2"},
                    },
                ],
                "edges": [{"id": "e1", "source": "1", "target": "2"}],
            }
        )
        result = get_widget_page_context(pipeline)
        for node in result["pipeline_structure"]["nodes"]:
            assert "position" not in node
            assert "id" in node
            assert "data" in node
        assert result["pipeline_structure"]["edges"] == [{"id": "e1", "source": "1", "target": "2"}]

    def test_serializes_static_triggers(self):
        pipeline = PipelineFactory()
        experiment = ExperimentFactory(pipeline=pipeline, team=pipeline.team)
        action = EventActionFactory(action_type=EventActionType.LOG, params={"key": "value"})
        StaticTriggerFactory(
            experiment=experiment,
            action=action,
            type=StaticTriggerType.NEW_HUMAN_MESSAGE,
            is_active=True,
        )

        result = get_widget_page_context(pipeline, experiment=experiment)
        static_triggers = result["event_triggers"]["static_triggers"]
        assert len(static_triggers) == 1
        assert static_triggers[0]["type"] == StaticTriggerType.NEW_HUMAN_MESSAGE
        assert static_triggers[0]["type_label"] == StaticTriggerType.NEW_HUMAN_MESSAGE.label
        assert "is_active" not in static_triggers[0]
        assert static_triggers[0]["action"]["action_type"] == EventActionType.LOG
        assert static_triggers[0]["action"]["action_type_label"] == EventActionType.LOG.label
        assert static_triggers[0]["action"]["params"] == {"key": "value"}

    def test_serializes_timeout_triggers(self):
        pipeline = PipelineFactory()
        experiment = ExperimentFactory(pipeline=pipeline, team=pipeline.team)
        action = EventActionFactory(action_type=EventActionType.LOG)
        TimeoutTriggerFactory(
            experiment=experiment,
            action=action,
            delay=30,
            total_num_triggers=5,
            is_active=True,
        )

        result = get_widget_page_context(pipeline, experiment=experiment)
        timeout_triggers = result["event_triggers"]["timeout_triggers"]
        assert len(timeout_triggers) == 1
        assert timeout_triggers[0]["delay"] == 30
        assert timeout_triggers[0]["total_num_triggers"] == 5
        assert "is_active" not in timeout_triggers[0]
        assert timeout_triggers[0]["action"]["action_type"] == EventActionType.LOG

    def test_excludes_inactive_triggers(self):
        pipeline = PipelineFactory()
        experiment = ExperimentFactory(pipeline=pipeline, team=pipeline.team)
        StaticTriggerFactory(experiment=experiment, is_active=False)
        TimeoutTriggerFactory(experiment=experiment, is_active=False)

        result = get_widget_page_context(pipeline, experiment=experiment)
        assert result["event_triggers"]["static_triggers"] == []
        assert result["event_triggers"]["timeout_triggers"] == []

    def test_excludes_archived_triggers(self):
        pipeline = PipelineFactory()
        experiment = ExperimentFactory(pipeline=pipeline, team=pipeline.team)
        StaticTriggerFactory(experiment=experiment, is_archived=True)
        TimeoutTriggerFactory(experiment=experiment, is_archived=True)

        result = get_widget_page_context(pipeline, experiment=experiment)
        assert result["event_triggers"]["static_triggers"] == []
        assert result["event_triggers"]["timeout_triggers"] == []
