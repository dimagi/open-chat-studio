import pytest

from apps.chat.tests.test_routing import _make_experiment_with_routing
from apps.experiments.models import ExperimentRoute
from apps.pipelines.helper import convert_non_pipeline_experiment_to_pipeline


@pytest.mark.django_db()
@pytest.mark.parametrize("assistant_children", [True, False])
def test_migrate_experiment_with_children(assistant_children):
    experiment = _make_experiment_with_routing(assistant_children=assistant_children, with_terminal=True)
    children = list(experiment.children.all())
    convert_non_pipeline_experiment_to_pipeline(experiment)

    experiment.refresh_from_db()
    pipeline = experiment.pipeline
    nodes = pipeline.node_set.all()
    expected_child_type = "AssistantNode" if assistant_children else "LLMResponseWithPrompt"
    expected = {
        ("StartNode", "start"),
        ("EndNode", "end"),
        ("RouterNode", experiment.name),
    } | {(expected_child_type, child.name) for child in children}
    assert {(node.type, node.name) for node in nodes} == expected

    routes = ExperimentRoute.objects.get_all().select_related("child").filter(parent=experiment)
    assert all(route.is_archived for route in routes)
    assert all(route.child.is_archived for route in routes)
