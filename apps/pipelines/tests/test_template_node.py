import pytest

from apps.experiments.models import Participant
from apps.pipelines.exceptions import PipelineNodeRunError
from apps.pipelines.nodes.base import PipelineState
from apps.pipelines.nodes.nodes import RenderTemplate
from apps.pipelines.repository import InMemoryPipelineRepository
from apps.utils.factories.experiment import ExperimentSessionFactory


@pytest.mark.django_db()
def test_render_template_with_context_keys():
    experiment_session = ExperimentSessionFactory.build()
    participant = Participant(
        identifier="participant_123",
        team=experiment_session.team,
        platform="web",
    )
    experiment_session.participant = participant
    state = PipelineState(
        experiment_session=experiment_session,
        messages=["Cycling"],
        temp_state={"my_key": "example_key"},
        outputs={},
        participant_data={"custom_key": "custom_value"},
        input_message_url="https://example.com/",
    )
    template = (
        "input: {{input}}, inputs: {{node_inputs}}, temp_state.my_key: {{temp_state.my_key}}, "
        "participant_id: {{participant_details.identifier}}, "
        "participant_data: {{participant_data.custom_key}}, "
        "input_message_url: {{input_message_url}} "
    )
    node = RenderTemplate(name="test", node_id="123", django_node=None, template_string=template)
    config = {"configurable": {"repo": InMemoryPipelineRepository(session=experiment_session)}}
    node_output = node.process(incoming_nodes=[], outgoing_nodes=[], state=state, config=config)
    assert node_output["messages"][-1] == (
        "input: Cycling, inputs: ['Cycling'], temp_state.my_key: example_key, "
        "participant_id: participant_123, "
        "participant_data: custom_value, "
        "input_message_url: https://example.com/ "
    )


def test_render_template_undefined_variable_error():
    experiment_session = ExperimentSessionFactory.build()
    state = PipelineState(
        experiment_session=experiment_session,
        messages=["hello"],
        outputs={},
    )
    node = RenderTemplate(name="test", node_id="123", django_node=None, template_string="{{ nonexistent_var }}")
    config = {"configurable": {"repo": InMemoryPipelineRepository(session=experiment_session)}}

    with pytest.raises(PipelineNodeRunError, match=r'UndefinedError in field "template_string"'):
        node.process(incoming_nodes=[], outgoing_nodes=[], state=state, config=config)
