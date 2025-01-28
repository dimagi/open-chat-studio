import datetime
import json

from django.core.serializers.json import DjangoJSONEncoder

from apps.channels.datamodels import Attachment
from apps.experiments.models import ExperimentSession
from apps.pipelines.nodes.base import PipelineState


def test_pipline_state_json_serializable():
    state = PipelineState(
        messages=["a", "b", "c"],
        outputs={"a": "a", "b": "b", "c": "c"},
        experiment_session=ExperimentSession(id=1),
        pipeline_version=1,
        shared_state={
            "user_input": "input",
            "outputs": {"a": "a", "b": "b", "c": "c"},
            "attachments": [Attachment(file_id=1, type="code_interpreter", name="file1", size=5)],
            "date": datetime.datetime.now(datetime.UTC),
        },
    ).json_safe()
    assert json.dumps(state, cls=DjangoJSONEncoder)
