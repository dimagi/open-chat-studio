"""Trace-status behavior of ChannelBase.new_user_message."""

from unittest.mock import patch

import pytest

from apps.channels.pipeline import MessageProcessingPipeline
from apps.channels.stages.base import ProcessingStage
from apps.channels.tests.message_examples import base_messages
from apps.chat.exceptions import UserActionableError
from apps.chat.models import ChatMessage
from apps.service_providers.llm_service.runnables import GenerationCancelled
from apps.utils.factories.experiment import ExperimentSessionFactory

from .conftest import StubChannel, make_trace_service


@pytest.mark.django_db()
def test_generation_cancelled_closes_trace_without_error():
    """A cancelled generation is control flow -- the trace must close cleanly.

    Regression: previously GenerationCancelled propagated out of pipeline.process
    and through the trace context manager, marking the whole trace as errored
    before being caught.
    """
    session = ExperimentSessionFactory.create()
    channel = StubChannel(session.experiment, session.experiment_channel, session)
    trace_service = make_trace_service()
    channel.trace_service = trace_service
    trace_cm = trace_service.trace.return_value

    with patch.object(MessageProcessingPipeline, "process", side_effect=GenerationCancelled(output="")):
        response = channel.new_user_message(base_messages.text_message())

    assert isinstance(response, ChatMessage)
    assert response.content == ""
    # No exception propagated through the trace context manager.
    trace_cm.__exit__.assert_called_once_with(None, None, None)
    trace_cm.set_outputs.assert_called_once_with({"response": "", "cancelled": True})


class _RaisesUserActionableError(ProcessingStage):
    def process(self, ctx):
        raise UserActionableError("`x.bmp` is not a supported image type")


@pytest.mark.django_db()
@patch.object(MessageProcessingPipeline, "_generate_error_message")
def test_user_actionable_error_closes_trace_without_error(mock_generate):
    """An error the participant can fix is answered, not reported as a broken trace.

    The pipeline already generates the participant-facing message, so new_user_message
    must return it instead of letting the exception mark the trace and fail the task.
    """
    mock_generate.return_value = "That image type is not supported -- try a PNG."
    session = ExperimentSessionFactory.create()
    channel = StubChannel(session.experiment, session.experiment_channel, session)
    trace_service = make_trace_service()
    channel.trace_service = trace_service
    trace_cm = trace_service.trace.return_value
    pipeline = MessageProcessingPipeline(core_stages=[_RaisesUserActionableError()], terminal_stages=[])

    with patch.object(StubChannel, "_build_pipeline", return_value=pipeline):
        response = channel.new_user_message(base_messages.text_message())

    assert isinstance(response, ChatMessage)
    assert response.content == "That image type is not supported -- try a PNG."
    trace_cm.__exit__.assert_called_once_with(None, None, None)
