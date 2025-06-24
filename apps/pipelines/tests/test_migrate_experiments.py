import pytest

from apps.chat.tests.test_routing import _make_experiment_with_routing
from apps.pipelines.helper import convert_non_pipeline_experiment_to_pipeline


@pytest.mark.django_db()
def test_migrate_experiment_with_children():
    experiment = _make_experiment_with_routing(with_terminal=True)
    children = experiment.children.all()
    convert_non_pipeline_experiment_to_pipeline(experiment)

    experiment.refresh_from_db()
    pipeline = experiment.pipeline
    nodes = pipeline.node_set.all()
    expected = {
        ("StartNode", "start"),
        ("EndNode", "end"),
        ("RouterNode", experiment.name),
    } | {("LLMResponseWithPrompt", child.name) for child in children}
    assert {(node.type, node.name) for node in nodes} == expected
