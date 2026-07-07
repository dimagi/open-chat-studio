"""Trace-status behavior of ChannelBase.new_user_message."""

from unittest.mock import patch

import pytest

from apps.channels.pipeline import MessageProcessingPipeline
from apps.channels.tests.message_examples import base_messages
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
