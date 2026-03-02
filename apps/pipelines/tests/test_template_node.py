import pytest

from apps.experiments.models import Participant
from apps.pipelines.nodes.base import PipelineState
from apps.pipelines.nodes.nodes import RenderTemplate
from apps.pipelines.repository import ORMRepository
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.pipelines import PipelineFactory


@pytest.fixture()
def pipeline():
    return PipelineFactory()


@pytest.fixture()
def experiment_session():
    return ExperimentSessionFactory()


@pytest.mark.django_db()
def test_render_template_with_context_keys(pipeline, experiment_session):
    participant = Participant.objects.create(
        identifier="participant_123",
        team=experiment_session.team,
        platform="web",
    )
    experiment_session.participant = participant
    experiment_session.save()
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
    config = {"configurable": {"repo": ORMRepository(session=experiment_session)}}
    node_output = node.process(incoming_nodes=[], outgoing_nodes=[], state=state, config=config)
    assert node_output["messages"][-1] == (
        "input: Cycling, inputs: ['Cycling'], temp_state.my_key: example_key, "
        "participant_id: participant_123, "
        "participant_data: custom_value, "
        "input_message_url: https://example.com/ "
    )
