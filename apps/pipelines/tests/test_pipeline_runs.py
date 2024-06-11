from unittest.mock import patch

import pytest
from langchain_core.runnables import RunnableLambda

from apps.experiments.models import ExperimentSession
from apps.pipelines.models import Pipeline, PipelineRunStatus
from apps.pipelines.nodes.base import PipelineNode, PipelineState
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.pipelines import PipelineFactory


@pytest.fixture()
def pipeline():
    return PipelineFactory()


@pytest.fixture()
def session():
    return ExperimentSessionFactory()


@pytest.mark.django_db()
def test_running_pipeline_creates_run(pipeline: Pipeline):
    input = "foo"
    pipeline.invoke(PipelineState(messages=[input]))
    assert pipeline.runs.count() == 1
    run = pipeline.runs.first()
    assert run.status == PipelineRunStatus.SUCCESS

    assert run.input == PipelineState(messages=[input])
    assert run.output == PipelineState(
        messages=[
            input,  # the input
            input,  # the output of the first node / input to the second
            input,  # the output
        ]
    )

    assert len(run.log["entries"]) == 8
    assert run.log["entries"][1]["level"] == "INFO"
    assert run.log["entries"][1]["message"] == "Passthrough starting"
    assert run.log["entries"][2]["level"] == "DEBUG"
    assert run.log["entries"][2]["message"] == f"Returning input: '{input}' without modification"
    assert run.log["entries"][3]["message"] == f"Passthrough finished: {input}"
    assert run.log["entries"][4]["message"] == "Passthrough starting"
    assert run.log["entries"][5]["message"] == f"Returning input: '{input}' without modification"
    assert run.log["entries"][6]["message"] == f"Passthrough finished: {input}"


@pytest.mark.django_db()
def test_running_failed_pipeline_logs_error(pipeline: Pipeline):
    input = "What's up"
    error_message = "Bad things are afoot"

    class FailingPassthrough(PipelineNode):
        def get_runnable(self, node) -> RunnableLambda:
            def fn(input, config):
                raise Exception(error_message)

            return RunnableLambda(fn, name=self.__class__.__name__)

    from apps.pipelines.nodes import nodes

    with patch.object(nodes, "Passthrough", FailingPassthrough):
        with pytest.raises(Exception, match=error_message):
            pipeline.invoke(PipelineState(messages=[input]))

    assert pipeline.runs.count() == 1
    run = pipeline.runs.first()
    assert run.input == PipelineState(messages=[input])
    assert run.output is None
    assert run.status == PipelineRunStatus.ERROR
    assert run.log["entries"][1]["level"] == "ERROR"
    assert run.log["entries"][1]["message"] == error_message


@pytest.mark.django_db()
def test_running_pipeline_stores_session(pipeline: Pipeline, session: ExperimentSession):
    input = "foo"
    pipeline.invoke(PipelineState(messages=[input]), session)
    assert pipeline.runs.count() == 1
    assert pipeline.runs.first().session_id == session.id

    pipeline.invoke(PipelineState(messages=[input]))
    assert pipeline.runs.count() == 2
    assert pipeline.runs.last().session_id is None
