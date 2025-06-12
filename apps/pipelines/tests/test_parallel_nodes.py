import pytest

from apps.pipelines.exceptions import PipelineBuildError
from apps.pipelines.nodes.base import PipelineState
from apps.pipelines.tests.utils import (
    code_node,
    create_runnable,
    end_node,
    passthrough_node,
    render_template_node,
    start_node,
)
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.pipelines import PipelineFactory
from apps.utils.pytest import django_db_with_data


@pytest.fixture()
def pipeline():
    return PipelineFactory()


@pytest.fixture()
def experiment(pipeline):
    return ExperimentFactory(team=pipeline.team, pipeline=pipeline)


@pytest.fixture()
def experiment_session(experiment):
    return ExperimentSessionFactory(experiment=experiment)


@django_db_with_data(available_apps=("apps.service_providers",))
def test_parallel_node_validation(pipeline):
    start = start_node()
    passthrough_1 = passthrough_node(name="1")
    passthrough_2 = passthrough_node(name="2")
    end = end_node()
    nodes = [start, passthrough_1, passthrough_2, end]
    edges = ["start - 1", "start - 2", "1 - end", "2 - end"]
    with pytest.raises(PipelineBuildError, match="Multiple edges connected to the same output"):
        create_runnable(pipeline, nodes, edges, lenient=False)


@django_db_with_data(available_apps=("apps.service_providers",))
def test_parallel_branch_pipeline(pipeline, experiment_session):
    start = start_node()
    template_a = render_template_node("A ({{ input }})", name="A")
    template_b = render_template_node("B ({{ input }})", name="B")
    template_c = render_template_node("C ({{ input }})", name="C")
    end = end_node()
    nodes = [start, template_a, template_b, template_c, end]
    edges = ["start - A", "start - B", "B - C", "A - end", "C - end"]
    user_input = "The Input"
    output = create_runnable(pipeline, nodes, edges, lenient=True).invoke(
        PipelineState(messages=[user_input], experiment_session=experiment_session)
    )["outputs"]
    expected_output = {
        "start": {"message": user_input, "node_id": start["id"]},
        template_a["params"]["name"]: {"message": f"A ({user_input})", "node_id": template_a["id"]},
        template_b["params"]["name"]: {"message": f"B ({user_input})", "node_id": template_b["id"]},
        template_c["params"]["name"]: {"message": f"C (B ({user_input}))", "node_id": template_c["id"]},
        "end": [
            {"message": f"A ({user_input})", "node_id": end["id"]},
            {"message": f"C (B ({user_input}))", "node_id": end["id"]},
        ],
    }
    assert output == expected_output


# @django_db_with_data(available_apps=("apps.service_providers",))
@pytest.mark.django_db()
@pytest.mark.parametrize("safety_check", ["safe", "unsafe"])
def test_code_node_abort(pipeline, experiment_session, safety_check):
    start = start_node()
    node_a = static_code_router(safety_check, "safety_check")
    node_b = static_code_router("B", "B")
    code = code_node(
        code="""
def main(input, **kwargs):
    safety_check = get_node_output("safety_check")
    b = get_node_output("B")
    if safety_check == "safe":
        return b
    return Abort(f"Unsafe input: {safety_check}")
    """,
        name="Code",
    )
    node_c = passthrough_node(name="C")
    end = end_node()
    nodes = [start, node_a, node_b, code, node_c, end]
    edges = ["start - safety_check", "start - B", "safety_check - Code", "B - Code", "Code - C", "C - end"]
    user_input = "The Input"
    output = create_runnable(pipeline, nodes, edges, lenient=True).invoke(
        PipelineState(messages=[user_input], experiment_session=experiment_session)
    )
    output_state = PipelineState(output)
    if safety_check == "safe":
        assert output_state.get_node_output_by_name("end") == "B"
        assert "C" in output_state["outputs"]
    else:
        assert output_state["__interrupt__"][0].value == "Unsafe input: unsafe"
        assert "C" not in output_state["outputs"]


def static_code_router(output: str, name: str):
    return code_node(
        code=f"""
def main(input, **kwargs):
    return "{output}"
    """,
        name=name,
    )
