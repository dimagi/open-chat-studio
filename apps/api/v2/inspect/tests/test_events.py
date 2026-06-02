import pytest

from apps.api.v2.inspect.events import walk_events
from apps.api.v2.inspect.node_walker import LLM_PROVIDER, LLM_PROVIDER_MODEL
from apps.events.models import EventActionType
from apps.utils.factories.events import EventActionFactory, StaticTriggerFactory, TimeoutTriggerFactory
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.pipelines import NodeFactory, PipelineFactory
from apps.utils.factories.team import TeamFactory


@pytest.mark.django_db()
def test_timeout_trigger_delay_renamed_to_seconds():
    experiment = ExperimentFactory.create()
    TimeoutTriggerFactory.create(
        experiment=experiment,
        delay=86400,
        total_num_triggers=1,
        action=EventActionFactory.create(
            action_type=EventActionType.SEND_MESSAGE_TO_BOT, params={"message_to_bot": "Are you there?"}
        ),
    )
    walk = walk_events(experiment)
    assert len(walk.timeout_triggers) == 1
    trigger = walk.timeout_triggers[0]
    assert trigger.fields["delay_seconds"] == 86400
    assert trigger.fields["is_active"] is True
    assert trigger.action.type == EventActionType.SEND_MESSAGE_TO_BOT
    assert trigger.action.params == {"message_to_bot": "Are you there?"}
    assert trigger.action.pipeline is None


@pytest.mark.django_db()
def test_schedule_trigger_surfaces_cadence():
    experiment = ExperimentFactory.create()
    StaticTriggerFactory.create(
        experiment=experiment,
        action=EventActionFactory.create(
            action_type=EventActionType.SCHEDULETRIGGER,
            params={
                "name": "Daily nudge",
                "frequency": 1,
                "time_period": "days",
                "repetitions": 3,
                "prompt_text": "check in",
                "experiment_id": 999,
            },
        ),
    )
    walk = walk_events(experiment)
    cadence = walk.static_triggers[0].action.params["scheduled_message"]
    assert cadence == {
        "name": "Daily nudge",
        "frequency": 1,
        "time_period": "days",
        "repetitions": 3,
        "prompt_text": "check in",
    }
    # Non-cadence params (experiment_id) are not leaked into the projection.
    assert "experiment_id" not in cadence


@pytest.mark.django_db()
def test_pipeline_start_embeds_pipeline_and_accumulates_refs():
    team = TeamFactory.create()
    experiment = ExperimentFactory.create(team=team)
    pipeline = PipelineFactory.create(team=team)
    NodeFactory.create(
        pipeline=pipeline,
        type="LLMResponseWithPrompt",
        label="Completion",
        params={"llm_provider_id": 5, "llm_provider_model_id": 9},
    )
    StaticTriggerFactory.create(
        experiment=experiment,
        action=EventActionFactory.create(
            action_type=EventActionType.PIPELINE_START,
            params={"pipeline_id": pipeline.id, "input_type": "last_message"},
        ),
    )
    walk = walk_events(experiment)
    action = walk.static_triggers[0].action
    assert action.type == EventActionType.PIPELINE_START
    # pipeline_id is consumed; the pipeline is embedded as a full walk
    assert "pipeline_id" not in action.params
    assert action.params == {"input_type": "last_message"}
    assert action.pipeline is not None
    assert any(node.type == "LLMResponseWithPrompt" for node in action.pipeline.nodes)
    # the embedded pipeline's resource references bubble up for batch loading
    assert walk.resource_refs[LLM_PROVIDER] == {5}
    assert walk.resource_refs[LLM_PROVIDER_MODEL] == {9}


@pytest.mark.django_db()
def test_pipeline_start_cross_team_pipeline_not_embedded():
    experiment = ExperimentFactory.create()
    other_pipeline = PipelineFactory.create(team=TeamFactory.create())
    StaticTriggerFactory.create(
        experiment=experiment,
        action=EventActionFactory.create(
            action_type=EventActionType.PIPELINE_START,
            params={"pipeline_id": other_pipeline.id, "input_type": "last_message"},
        ),
    )
    walk = walk_events(experiment)
    # A cross-team pipeline id resolves to nothing rather than leaking another team's pipeline.
    assert walk.static_triggers[0].action.pipeline is None


@pytest.mark.django_db()
def test_archived_triggers_excluded():
    experiment = ExperimentFactory.create()
    StaticTriggerFactory.create(experiment=experiment, is_archived=True)
    StaticTriggerFactory.create(experiment=experiment, is_archived=False)
    walk = walk_events(experiment)
    assert len(walk.static_triggers) == 1
