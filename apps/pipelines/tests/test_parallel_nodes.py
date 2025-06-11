import pytest

from apps.pipelines.exceptions import PipelineBuildError
from apps.pipelines.nodes.base import PipelineState
from apps.pipelines.tests.utils import (
    create_runnable,
    end_node,
    passthrough_node,
    render_template_node,
    start_node,
)
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.pipelines import PipelineFactory
from apps.utils.pytest import django_db_with_data


@pytest.fixture()
def pipeline():
    return PipelineFactory()


@pytest.fixture()
def experiment_session():
    return ExperimentSessionFactory()


@django_db_with_data(available_apps=("apps.service_providers",))
def test_parallel_node_validation(pipeline):
    start = start_node()
    passthrough_1 = passthrough_node()
    passthrough_2 = passthrough_node()
    end = end_node()
    nodes = [start, passthrough_1, passthrough_2, end]
    edges = [
        {
            "id": "start -> passthrough 1",
            "source": start["id"],
            "target": passthrough_1["id"],
        },
        {
            "id": "start -> passthrough 2",
            "source": start["id"],
            "target": passthrough_2["id"],
        },
        {
            "id": "passthrough 1 -> end",
            "source": passthrough_1["id"],
            "target": end["id"],
        },
        {
            "id": "passthrough 2 -> end",
            "source": passthrough_2["id"],
            "target": end["id"],
        },
    ]

    with pytest.raises(PipelineBuildError, match="Multiple edges connected to the same output"):
        create_runnable(pipeline, nodes, edges, lenient=False)


@django_db_with_data(available_apps=("apps.service_providers",))
def test_branching_pipeline(pipeline, experiment_session):
    start = start_node()
    template_a = render_template_node("A ({{input }})")
    template_b = render_template_node("B ({{ input}})")
    template_c = render_template_node("C ({{input }})")
    end = end_node()
    nodes = [
        start,
        template_a,
        template_b,
        template_c,
        end,
    ]
    edges = [
        {
            "id": "start -> RenderTemplate-A",
            "source": start["id"],
            "target": template_a["id"],
        },
        {
            "id": "start -> RenderTemplate-B",
            "source": start["id"],
            "target": template_b["id"],
        },
        {
            "id": "RenderTemplate-B -> RenderTemplate-C",
            "source": template_b["id"],
            "target": template_c["id"],
        },
        {
            "id": "RenderTemplate-A -> END",
            "source": template_a["id"],
            "target": end["id"],
        },
        {
            "id": "RenderTemplate-C -> END",
            "source": template_c["id"],
            "target": end["id"],
        },
    ]
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
