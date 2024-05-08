import pytest
from langchain_core.runnables import RunnableConfig

from apps.pipelines.graph import PipelineGraph
from apps.pipelines.utils import build_runnable
from apps.utils.factories.experiment import ExperimentSessionFactory


@pytest.fixture()
def session():
    return ExperimentSessionFactory()


# # Report = Compound Node (has many runnables)
# parms:
# -> prompt to create report (build time)
# -> template to render it (build time)

# build:
# LLMStep() | TemplateStep()


# # Email = Simple Custom Node (returns a single runnable)
# params:
# -> list of emails (build time)
# invoke:
# enqueues a celery task to send the email


# - Add event to trigger pipeline


@pytest.mark.django_db()
def test_llm_response(session):
    graph = PipelineGraph.from_json(
        {
            "data": {
                "edges": [],
                "nodes": [
                    {
                        "data": {
                            "id": "llm-GUk0C",
                            "label": "RenderTemplate",
                            "type": "LLMResponse",
                        },
                        "id": "llm-GUk0C",
                        "position": {"x": 478.55002422163244, "y": 87.74100575049786},
                        "selected": False,
                        "type": "pipelineNode",
                    },
                ],
                "viewport": {"x": 289.4160274478008, "y": 109.38674127734322, "zoom": 0.6224371176910848},
            },
            "id": 1,
            "name": "New Pipeline",
        }
    )
    with pytest.raises(ValueError, match="session_id"):
        build_runnable(graph)
    build_runnable(graph, session_id=session.id)
    # Having trouble testing this with the fake_llm tools, since we refetch the session from the db


def test_create_report():
    graph = PipelineGraph.from_json(
        {
            "data": {
                "edges": [],
                "nodes": [
                    {
                        "data": {
                            "id": "llm-GUk0C",
                            "label": "RenderTemplate",
                            "type": "CreateReport",
                            "params": {
                                "template_string": "{{ stuff }} is cool",
                            },
                        },
                        "id": "llm-GUk0C",
                        "position": {"x": 478.55002422163244, "y": 87.74100575049786},
                        "selected": False,
                        "type": "pipelineNode",
                    },
                ],
                "viewport": {"x": 289.4160274478008, "y": 109.38674127734322, "zoom": 0.6224371176910848},
            },
            "id": 1,
            "name": "New Pipeline",
        }
    )
    runnable = build_runnable(graph)
    assert (
        runnable.invoke({"input": "The rain in spain"}).text
        == "Make a summary of the following chat: The rain in spain"
    )
    assert (
        runnable.invoke(
            {"input": "elephant"}, config=RunnableConfig(configurable={"prompt": "Do a dance with: {input}"})
        ).text
        == "Do a dance with: elephant"
    )


def test_render_template():
    graph = PipelineGraph.from_json(
        {
            "data": {
                "edges": [],
                "nodes": [
                    {
                        "data": {
                            "id": "llm-GUk0C",
                            "label": "RenderTemplate",
                            "type": "RenderTemplate",
                            "params": {
                                "template_string": "{{ stuff }} is cool",
                            },
                        },
                        "id": "llm-GUk0C",
                        "position": {"x": 478.55002422163244, "y": 87.74100575049786},
                        "selected": False,
                        "type": "pipelineNode",
                    },
                ],
                "viewport": {"x": 289.4160274478008, "y": 109.38674127734322, "zoom": 0.6224371176910848},
            },
            "id": 1,
            "name": "New Pipeline",
        }
    )
    runnable = build_runnable(graph)

    assert runnable.invoke({"stuff": "Elephants"}) == "Elephants is cool"
    assert (
        runnable.invoke(
            {"stuff": "elephant"}, config=RunnableConfig(configurable={"template_string": "Hello {{stuff }}"})
        )
        == "Hello elephant"
    )
