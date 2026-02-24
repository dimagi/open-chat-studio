import datetime
import json

from django.core.serializers.json import DjangoJSONEncoder

from apps.channels.datamodels import Attachment
from apps.experiments.models import ExperimentSession
from apps.pipelines.nodes.base import PipelineState
from apps.pipelines.nodes.context import PipelineAccessor


def test_pipline_state_json_serializable():
    state = PipelineState(
        messages=["a", "b", "c"],
        outputs={"a": "a", "b": "b", "c": "c"},
        experiment_session=ExperimentSession(id=1),
        temp_state={
            "user_input": "input",
            "outputs": {"a": "a", "b": "b", "c": "c"},
            "attachments": [Attachment(file_id=1, type="code_interpreter", name="file1", size=5, download_link="")],
            "date": datetime.datetime.now(datetime.UTC),
        },
    ).json_safe()
    assert json.dumps(state, cls=DjangoJSONEncoder)


def test_route_info():
    state = PipelineState(
        messages=["a", "b", "c"],
        outputs={
            "node1": {
                "node_id": "1",
                "message": "a",
            },
            "node2": {"node_id": "2", "output_handle": "output0", "message": "b", "route": "B"},
            "node3": {
                "node_id": "3",
                "message": "c",
            },
            "node4": {"node_id": "4", "output_handle": "output1", "message": "d", "route": "A"},
        },
        path=[
            (None, "1", ["2"]),
            ("1", "2", ["3"]),
            ("2", "3", ["4"]),
            ("3", "4", []),
        ],
        experiment_session=ExperimentSession(id=1),
        pipeline_version=1,
        temp_state={},
    )
    accessor = PipelineAccessor(state)
    assert accessor.get_all_routes() == {"node2": "B", "node4": "A"}

    assert accessor.get_selected_route("node4") == "A"
    assert accessor.get_selected_route("node3") is None
    assert accessor.get_node_path("node4") == ["node1", "node2", "node3", "node4"]
