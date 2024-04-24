import pytest
from langchain_core.runnables import RunnableConfig

from apps.pipelines.graph import PipelineGraph
from apps.pipelines.utils import build_runnable
from apps.utils.factories.experiment import ExperimentSessionFactory


@pytest.fixture()
def session():
    return ExperimentSessionFactory()


@pytest.mark.django_db()
def test_simple_graph(session):
    graph = PipelineGraph.from_json(
        {
            "data": {
                "edges": [],
                "nodes": [
                    {
                        "data": {"id": "llm-GUk0C", "label": "Session", "type": "session"},
                        "id": "llm-GUk0C",
                        "position": {"x": 478.55002422163244, "y": 87.74100575049786},
                        "selected": False,
                        "type": "pipelineNode",
                    },
                    {
                        "data": {"id": "llm-GUk0C", "label": "Prompt", "type": "prompt"},
                        "id": "llm-GUk0C",
                        "position": {"x": 478.55002422163244, "y": 87.74100575049786},
                        "selected": False,
                        "type": "pipelineNode",
                    },
                    {
                        "data": {
                            "id": "llm-GUk0C",
                            "label": "LLM",
                            "type": "llm",
                            "params": {
                                "openai_api_key": "sk-proj-xx",
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
    print(runnable.invoke({"topic": "elephant"}, config=RunnableConfig(configurable={"session_id": session.id})))
