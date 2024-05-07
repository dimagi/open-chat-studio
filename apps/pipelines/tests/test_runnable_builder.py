from langchain_core.runnables import RunnableConfig

from apps.pipelines.graph import PipelineGraph
from apps.pipelines.utils import build_runnable


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
