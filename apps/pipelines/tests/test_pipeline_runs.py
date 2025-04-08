from unittest.mock import patch

import pytest
from langchain_core.runnables import RunnableLambda

from apps.channels.datamodels import Attachment
from apps.chat.models import ChatMessageType
from apps.experiments.models import ExperimentSession
from apps.pipelines.models import LogEntry, Pipeline, PipelineRunStatus
from apps.pipelines.nodes.base import PipelineNode, PipelineState
from apps.pipelines.nodes.nodes import StartNode
from apps.service_providers.models import TraceProvider
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.pipelines import PipelineFactory
from apps.utils.pytest import django_db_transactional


@pytest.fixture()
def pipeline():
    return PipelineFactory()


@pytest.fixture()
def session():
    return ExperimentSessionFactory()


@django_db_transactional()
def test_running_pipeline_creates_run(pipeline: Pipeline, session: ExperimentSession):
    input = "foo"
    attachments = [
        Attachment(file_id=123, type="code_interpreter", name="test.py", size=10),
    ]
    serialized_attachments = [att.model_dump() for att in attachments]
    pipeline.invoke(PipelineState(messages=[input], attachments=serialized_attachments), session)
    assert pipeline.runs.count() == 1
    run = pipeline.runs.first()
    assert run.status == PipelineRunStatus.SUCCESS

    assert run.input == PipelineState(messages=[input], attachments=serialized_attachments)
    assert run.output == PipelineState(
        attachments=[
            {
                "content_type": "application/octet-stream",
                "file_id": 123,
                "name": "test.py",
                "size": 10,
                "type": "code_interpreter",
                "upload_to_assistant": False,
            }
        ],
        messages=[
            input,  # the input to the graph
            input,  # The output of the start node
            input,  # the output of the end node
        ],
        outputs={
            pipeline.node_ids[0]: {"message": "foo"},
            pipeline.node_ids[1]: {"message": "foo"},
        },
        temp_state={
            "outputs": {"end": "foo", "start": "foo"},
            "user_input": "foo",
            "attachments": serialized_attachments,
        },
        input_message_metadata={},
        output_message_metadata={},
    )

    assert len(run.log["entries"]) == 8

    entries = run.log["entries"]
    assert LogEntry(**entries[0]) == LogEntry(
        time=entries[0]["time"],
        message="Starting pipeline run",
        level="DEBUG",
        input=input,
    )
    assert LogEntry(**entries[1]) == LogEntry(
        time=entries[1]["time"],
        message=f"{pipeline.node_ids[0]} starting",
        level="INFO",
        input=input,
    )
    assert LogEntry(**entries[2]) == LogEntry(
        time=entries[2]["time"],
        message=f"Returning input: '{input}' without modification",
        level="DEBUG",
        input=input,
        output=input,
    )
    assert LogEntry(**entries[3]) == LogEntry(
        time=entries[3]["time"],
        message=f"{pipeline.node_ids[0]} finished",
        level="INFO",
        output=input,
    )
    assert LogEntry(**entries[4]) == LogEntry(
        time=entries[4]["time"],
        message=f"{pipeline.node_ids[1]} starting",
        level="INFO",
        input=input,
    )
    assert LogEntry(**entries[5]) == LogEntry(
        time=entries[5]["time"],
        message=f"Returning input: '{input}' without modification",
        level="DEBUG",
        input=input,
        output=input,
    )
    assert LogEntry(**entries[6]) == LogEntry(
        time=entries[6]["time"],
        message=f"{pipeline.node_ids[1]} finished",
        level="INFO",
        output=input,
    )
    assert LogEntry(**entries[7]) == LogEntry(
        time=entries[7]["time"],
        message="Pipeline run finished",
        level="DEBUG",
        output=input,
    )


@django_db_transactional()
def test_running_failed_pipeline_logs_error(pipeline: Pipeline, session: ExperimentSession):
    input = "What's up"
    error_message = "Bad things are afoot"

    class FailingNode(PipelineNode):
        name: str = "failure"

        def process(self, *args, **kwargs) -> RunnableLambda:
            raise Exception(error_message)

    from apps.pipelines.nodes import nodes

    with patch.object(nodes, StartNode.__name__, FailingNode):
        with pytest.raises(Exception, match=error_message):
            pipeline.invoke(PipelineState(messages=[input]), session)

    assert pipeline.runs.count() == 1
    run = pipeline.runs.first()
    assert run.input == PipelineState(messages=[input])
    assert run.output is None
    assert run.status == PipelineRunStatus.ERROR
    entries = run.log["entries"]
    assert LogEntry(**entries[2]) == LogEntry(
        time=entries[2]["time"],
        message=error_message,
        level="ERROR",
    )
    assert LogEntry(**entries[3]) == LogEntry(
        time=entries[3]["time"],
        message=error_message,
        level="ERROR",
    )
    assert LogEntry(**entries[4]) == LogEntry(
        time=entries[4]["time"], message="Pipeline run failed", level="DEBUG", input=input
    )


@django_db_transactional()
def test_running_pipeline_stores_session(pipeline: Pipeline, session: ExperimentSession):
    input = "foo"
    pipeline.invoke(PipelineState(messages=[input]), session)
    assert pipeline.runs.count() == 1
    assert pipeline.runs.first().session_id == session.id


@django_db_transactional()
@pytest.mark.parametrize("save_input_to_history", [True, False])
def test_save_input_to_history(save_input_to_history, pipeline: Pipeline, session: ExperimentSession):
    input = "Hi"
    pipeline.invoke(PipelineState(messages=[input]), session, save_input_to_history=save_input_to_history)
    assert (
        session.chat.messages.filter(content="Hi", message_type=ChatMessageType.HUMAN).exists() == save_input_to_history
    )


@django_db_transactional()
def test_save_trace_metadata(pipeline: Pipeline, session: ExperimentSession):
    provider = TraceProvider(type="langfuse", config={})
    session.experiment.trace_provider = provider
    pipeline.invoke(PipelineState(messages=["Hi"]), session)
    human_message = session.chat.messages.filter(message_type=ChatMessageType.HUMAN).first()
    assert "trace_info" in human_message.metadata
    ai_message = session.chat.messages.filter(message_type=ChatMessageType.AI).first()
    assert "trace_info" in ai_message.metadata
