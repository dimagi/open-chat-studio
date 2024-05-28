import pytest

from apps.pipelines.models import Pipeline, PipelineRunStatus
from apps.pipelines.nodes.base import PipelineState
from apps.utils.factories.pipelines import PipelineFactory


@pytest.fixture()
def pipeline():
    return PipelineFactory()


@pytest.mark.django_db()
def test_running_pipeline_creates_run(pipeline: Pipeline):
    input = "foo"
    pipeline.invoke(PipelineState(messages=[input]))
    assert pipeline.runs.count() == 1
    run = pipeline.runs.first()
    assert run.status == PipelineRunStatus.SUCCESS
    assert len(run.log["entries"]) == 8
    assert run.log["entries"][1]["level"] == "INFO"
    assert run.log["entries"][1]["message"] == "Passthrough starting"
    assert run.log["entries"][2]["level"] == "DEBUG"
    assert run.log["entries"][2]["message"] == f"Returning input: '{input}' without modification"
    assert run.log["entries"][3]["message"] == f"Passthrough finished: {input}"
    assert run.log["entries"][4]["message"] == "Passthrough starting"
    assert run.log["entries"][5]["message"] == f"Returning input: '{input}' without modification"
    assert run.log["entries"][6]["message"] == f"Passthrough finished: {input}"
