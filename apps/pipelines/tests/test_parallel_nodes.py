import pytest

from apps.pipelines.nodes.base import PipelineState
from apps.pipelines.tests.utils import (
    code_node,
    create_runnable,
    end_node,
    passthrough_node,
    render_template_node,
    start_node,
    state_key_router_node,
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


@django_db_with_data()
def test_parallel_branch_pipeline(pipeline, experiment_session):
    """
    Illustrate and validate what happens with parallel branches.

    start -> A -----> end
          -> B -> C --^
    """
    start = start_node()
    template_a = render_template_node("A ({{ input }})", name="A")
    template_b = render_template_node("B ({{ input }})", name="B")
    template_c = render_template_node("C ({{ input }})", name="C")
    end = end_node()
    nodes = [start, template_a, template_b, template_c, end]
    edges = ["start - A", "start - B", "B - C", "A - end", "C - end"]
    user_input = "The Input"
    output = create_runnable(pipeline, nodes, edges).invoke(
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


@django_db_with_data()
def test_parallel_branch_with_merge(pipeline, experiment_session):
    """
    Illustrate and validate what happens with parallel branches with an aggregator node.

    start -> A -----> D -> end
          -> B -> C --^

    Node D gets called twice, once with the output from A and once with the output of C.
    'end' also gets called twice.
    """
    start = start_node()
    template_a = render_template_node("A ({{ input }})", name="A")
    template_b = render_template_node("B ({{ input }})", name="B")
    template_c = render_template_node("C ({{ input }})", name="C")
    template_d = render_template_node("D ({{ input }})", name="D")
    end = end_node()
    nodes = [start, template_a, template_b, template_c, template_d, end]
    edges = ["start - A", "start - B", "B - C", "A - D", "C - D", "D - end"]
    user_input = "The Input"
    output = create_runnable(pipeline, nodes, edges).invoke(
        PipelineState(messages=[user_input], experiment_session=experiment_session)
    )["outputs"]
    expected_output = {
        "start": {"message": user_input, "node_id": start["id"]},
        template_a["params"]["name"]: {"message": f"A ({user_input})", "node_id": template_a["id"]},
        template_b["params"]["name"]: {"message": f"B ({user_input})", "node_id": template_b["id"]},
        template_c["params"]["name"]: {"message": f"C (B ({user_input}))", "node_id": template_c["id"]},
        template_d["params"]["name"]: [
            {"message": f"D (A ({user_input}))", "node_id": template_d["id"]},
            {"message": f"D (C (B ({user_input})))", "node_id": template_d["id"]},
        ],
        "end": [
            {"message": f"D (A ({user_input}))", "node_id": end["id"]},
            {"message": f"D (C (B ({user_input})))", "node_id": end["id"]},
        ],
    }
    assert output == expected_output


@django_db_with_data()
def test_parallel_branch_with_dangling_node(pipeline, experiment_session):
    """Node A does not connect to the end node, but it is still executed.

    start -> B -> end
          -> A
    """
    start = start_node()
    template_a = render_template_node("A ({{ input }})", name="A")
    template_b = render_template_node("B ({{ input }})", name="B")
    end = end_node()
    nodes = [start, template_a, template_b, end]
    edges = ["start - A", "start - B", "B - end"]
    user_input = "The Input"
    output = create_runnable(pipeline, nodes, edges).invoke(
        PipelineState(messages=[user_input], experiment_session=experiment_session)
    )["outputs"]
    expected_output = {
        "start": {"message": user_input, "node_id": start["id"]},
        template_a["params"]["name"]: {"message": f"A ({user_input})", "node_id": template_a["id"]},
        template_b["params"]["name"]: {"message": f"B ({user_input})", "node_id": template_b["id"]},
        "end": {"message": f"B ({user_input})", "node_id": end["id"]},
    }
    assert output == expected_output


@pytest.mark.django_db()
@pytest.mark.parametrize("safety_check", ["safe", "unsafe"])
def test_code_node_abort(pipeline, experiment_session, safety_check):
    """
    Test that aborting in a code node prevents future node execution

    start -> B ------------> Code -> C -> end
          -> safety_check ---^
    """
    start = start_node()
    node_a = static_output(safety_check, "safety_check")
    node_b = static_output("B", "B")
    code = code_node(
        code="""
def main(input, **kwargs):
    safety_check = get_node_output("safety_check")
    b = get_node_output("B")
    if safety_check != "safe":
        abort_with_message(f"Unsafe input: {safety_check}", "unsafe_input")
    return b
    """,
        name="Code",
    )
    node_c = passthrough_node(name="C")
    end = end_node()
    nodes = [start, node_a, node_b, code, node_c, end]
    edges = ["start - safety_check", "start - B", "safety_check - Code", "B - Code", "Code - C", "C - end"]
    output = create_runnable(pipeline, nodes, edges).invoke(
        PipelineState(messages=["Hi"], experiment_session=experiment_session)
    )
    output_state = PipelineState(output)
    if safety_check == "safe":
        assert output_state.get_node_output_by_name("end") == "B"
        assert "C" in output_state["outputs"]
    else:
        assert output_state["__interrupt__"][0].value == {"message": "Unsafe input: unsafe", "tag_name": "unsafe_input"}
        assert "C" not in output_state["outputs"]
        json_safe = output_state.json_safe()
        assert "__interrupt__" not in json_safe
        assert json_safe["interrupt"] == {"message": "Unsafe input: unsafe", "tag_name": "unsafe_input"}


@django_db_with_data()
def test_code_node_wait_for_inputs(pipeline, experiment_session):
    """In this test the branches are of unequal length, so the code node will get called twice,
    once when A and C are done, and once when B is done.

    We want the code node to wait until all branches have completed before returning a result.

    start -> A -> B -> Code -> end
          -> C --------^
    """
    start = start_node()
    node_a = render_template_node("A: {{ input }}", "A")
    node_b = render_template_node("B: {{ input }}", "B")
    node_c = render_template_node("C: {{ input }}", "C")
    code = code_node(
        code="""
def main(input, **kwargs):
    require_node_outputs("B")
    c = get_node_output("C")
    b = get_node_output("B")  # expect this to arrive after C
    return f"{b},{c}"
    """,
        name="Code",
    )
    end = end_node()
    nodes = [start, node_a, node_b, code, node_c, end]
    edges = ["start - A", "start - C", "A - B", "B - Code", "C - Code", "Code - end"]
    output = create_runnable(pipeline, nodes, edges).invoke(
        PipelineState(messages=["Hi"], experiment_session=experiment_session)
    )
    output_state = PipelineState(output)
    assert output_state.get_node_output_by_name("end") == "B: A: Hi,C: Hi"
    assert isinstance(output_state["outputs"]["Code"], dict)
    assert output_state.get_execution_flow() in [
        [
            (None, "start", ["A", "C"]),
            ("start", "C", ["Code"]),
            ("start", "A", ["B"]),
            ("A", "B", ["Code"]),
            ("C", "Code", ["end"]),
            ("Code", "end", []),
        ],
        [
            (None, "start", ["A", "C"]),
            ("start", "A", ["B"]),
            ("start", "C", ["Code"]),
            ("A", "B", ["Code"]),
            ("C", "Code", ["end"]),
            ("Code", "end", []),
        ],
    ]


@django_db_with_data()
def test_code_node_wait_for_inputs_manually(pipeline, experiment_session):
    """Similar to the previous test but uses `wait_for_next_input`.

    start -> A -> B -> Code -> end
          -> C --------^
    """
    start = start_node()
    node_a = render_template_node("A: {{ input }}", "A")
    node_b = render_template_node("B: {{ input }}", "B")
    node_c = render_template_node("C: {{ input }}", "C")
    code = code_node(
        code="""
def main(input, **kwargs):
    c = get_node_output("C")
    b = get_node_output("B")
    if not (b and c):
        wait_for_next_input()
    return f"{b},{c}"
    """,
        name="Code",
    )
    end = end_node()
    nodes = [start, node_a, node_b, code, node_c, end]
    edges = ["start - A", "start - C", "A - B", "B - Code", "C - Code", "Code - end"]
    output = create_runnable(pipeline, nodes, edges).invoke(
        PipelineState(messages=["Hi"], experiment_session=experiment_session)
    )
    output_state = PipelineState(output)
    assert output_state.get_node_output_by_name("end") == "B: A: Hi,C: Hi"


@django_db_with_data()
def test_dangling_node_abort_terminates_early(pipeline, experiment_session):
    """Test that an abort from a dangling node does actually abort the pipeline.

    start -> A -> B -> end
          -> Code Abort
    """
    start = start_node()
    node_a = passthrough_node("A")
    node_b = passthrough_node("B")
    code = code_abort()
    end = end_node()
    nodes = [start, node_a, node_b, code, end]
    edges = ["start - Code Abort", "start - A", "A - B", "B - end"]
    output = create_runnable(pipeline, nodes, edges).invoke(
        PipelineState(messages=["Hi"], experiment_session=experiment_session)
    )
    output_state = PipelineState(output)
    assert "__interrupt__" in output_state
    # node B should not have been executed since it would run after the abort
    assert "B" not in output_state["outputs"]


@django_db_with_data()
@pytest.mark.parametrize("safety_check", ["safe", "unsafe"])
def test_safety_router_abort(pipeline, experiment_session, safety_check):
    """
    start -> A -> end
          -> Router -(unsafe)-> Code Abort
    """
    start = start_node()
    node_a = passthrough_node("A")
    router = state_key_router_node(route_key="is_safe", keywords=["safe", "unsafe"], name="Router")
    code = code_abort()
    end = end_node()
    nodes = [start, node_a, router, code, end]
    edges = ["start - A", "start - Router", "A - end", "Router:1 - Code Abort"]
    output = create_runnable(pipeline, nodes, edges).invoke(
        PipelineState(messages=["Hi"], experiment_session=experiment_session, temp_state={"is_safe": safety_check})
    )
    output_state = PipelineState(output)
    if safety_check == "safe":
        assert "__interrupt__" not in output_state
    else:
        assert "__interrupt__" in output_state


@django_db_with_data()
def test_dangling_node_abort_after(pipeline, experiment_session):
    """Test that an abort from a dangling node that is run after the last node still aborts.

    start -> A -> end
          -> B -> C -> Code Abort
    """
    start = start_node()
    node_a = passthrough_node("A")
    node_b = passthrough_node("B")
    node_c = passthrough_node("C")
    code = code_abort()
    end = end_node()
    nodes = [start, node_a, node_b, node_c, code, end]
    edges = ["start - A", "start - B", "A - end", "B - C", "C - Code Abort"]
    output = create_runnable(pipeline, nodes, edges).invoke(
        PipelineState(messages=["Hi"], experiment_session=experiment_session)
    )
    output_state = PipelineState(output)
    assert "__interrupt__" in output_state
    # node B should not have been executed since it would run after the abort
    assert "A" in output_state["outputs"]
    assert "B" in output_state["outputs"]
    assert "C" in output_state["outputs"]
    assert "Code Abort" not in output_state["outputs"]


def static_output(output: str, name: str):
    return code_node(
        code=f"""
def main(input, **kwargs):
    return "{output}"
    """,
        name=name,
    )


def code_abort(name="Code Abort"):
    return code_node(
        code="""
def main(input, **kwargs):
    abort_with_message("Abort!")
    """,
        name=name,
    )
