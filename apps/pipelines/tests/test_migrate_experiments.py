import pytest

from apps.experiments.models import ExperimentRoute, ExperimentRouteType
from apps.pipelines.helper import convert_non_pipeline_experiment_to_pipeline
from apps.utils.factories.assistants import OpenAiAssistantFactory
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamFactory


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


def _make_experiment_with_routing(with_default=True, assistant_children=False, with_terminal=False):
    team = TeamFactory()
    experiments = ExperimentFactory.create_batch(5 if with_terminal else 4, team=team, pipeline=None)
    router = experiments[0]
    if assistant_children:
        for exp in experiments[1:]:
            exp.assistant = OpenAiAssistantFactory(team=team)
            exp.save()

    children = [
        ExperimentRoute(team=team, parent=router, child=experiments[1], keyword="keyword1", is_default=False),
        ExperimentRoute(
            # make the middle one the default to avoid first / last false positives
            team=team,
            parent=router,
            child=experiments[2],
            keyword="keyword2",
            is_default=with_default,
        ),
        ExperimentRoute(team=team, parent=router, child=experiments[3], keyword="keyword3", is_default=False),
    ]
    if with_terminal:
        children.append(
            ExperimentRoute(team=team, parent=router, child=experiments[4], type=ExperimentRouteType.TERMINAL)
        )
    ExperimentRoute.objects.bulk_create(children)

    return router
