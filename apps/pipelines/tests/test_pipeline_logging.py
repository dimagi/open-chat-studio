import pytest

from apps.pipelines.models import Pipeline
from apps.pipelines.nodes.base import PipelineState
from apps.utils.factories.pipelines import PipelineFactory


@pytest.fixture()
def pipeline():
    return PipelineFactory()


@pytest.mark.django_db()
def test_running_pipeline_creates_run(pipeline: Pipeline):
    pipeline.invoke(PipelineState(messages=["hello"]))
    assert pipeline.runs.count() == 1
    run = pipeline.runs.first()
    assert run.status == "SUCCESS"
    assert len(run.log["entries"]) == 3
