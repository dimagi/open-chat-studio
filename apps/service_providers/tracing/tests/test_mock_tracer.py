from unittest.mock import MagicMock, patch
from uuid import uuid4

from apps.channels.datamodels import BaseMessage
from apps.service_providers.tracing.mock import MockTracer, RecordingTracerContextManager
from apps.service_providers.tracing.trace_service import TracingServiceWrapper


def test_mock_tracer_initialization():
    """Test that the mock tracer can be initialized and is in the ready state"""
    mock_tracer = MockTracer()
    assert mock_tracer.ready is True
    assert len(mock_tracer.initialize_calls) == 0


def test_mock_tracer_initialize():
    """Test recording initialize calls"""
    mock_tracer = MockTracer()
    trace_id = uuid4()
    mock_tracer.initialize("test-trace", trace_id, "session-123", "user-456")

    assert len(mock_tracer.initialize_calls) == 1
    call = mock_tracer.initialize_calls[0]
    assert call["trace_name"] == "test-trace"
    assert call["trace_id"] == trace_id
    assert call["session_id"] == "session-123"
    assert call["user_id"] == "user-456"


def test_mock_tracer_spans():
    """Test recording span starts and ends"""
    mock_tracer = MockTracer()

    # Start a span
    mock_tracer.start_span(
        span_id="span1", trace_name="process-message", inputs={"message": "hello"}, metadata={"channel": "web"}
    )

    # Check span was recorded
    assert "span1" in mock_tracer.span_starts
    span_data = mock_tracer.span_starts["span1"]
    assert span_data["trace_name"] == "process-message"
    assert span_data["inputs"]["message"] == "hello"
    assert span_data["metadata"]["channel"] == "web"

    # End the span
    mock_tracer.end_span(
        span_id="span1",
        outputs={"response": "Hi there!"},
    )

    # Check span end was recorded
    assert "span1" in mock_tracer.span_ends
    end_data = mock_tracer.span_ends["span1"]
    assert end_data["outputs"]["response"] == "Hi there!"
    assert end_data["error"] is None


def test_mock_tracer_events():
    """Test recording events"""
    mock_tracer = MockTracer()

    # Log an event
    mock_tracer.event(
        name="processing_error", message="Failed to process message", level="ERROR", metadata={"error_code": 500}
    )

    # Check event was recorded
    assert len(mock_tracer.events) == 1
    event = mock_tracer.events[0]
    assert event["name"] == "processing_error"
    assert event["message"] == "Failed to process message"
    assert event["level"] == "ERROR"
    assert event["metadata"]["error_code"] == 500


def test_tracing_service_wrapper_with_mock():
    """Test using the mock tracer with TracingServiceWrapper"""
    mock_tracer = MockTracer()
    wrapper = TracingServiceWrapper([mock_tracer])

    wrapper.initialize("session-id", "chat-conversation", "user-123")

    with wrapper.trace_context("span1", "process-message", {"text": "Hello"}):
        wrapper.set_outputs("span1", {"response": "Hi there"})
        wrapper.event("log", "Processing message", "DEBUG")

    # Verify initialize was called
    assert len(mock_tracer.initialize_calls) == 1
    assert mock_tracer.initialize_calls[0]["session_id"] == "session-id"

    # Verify span start/end
    assert "span1" in mock_tracer.span_starts
    assert "span1" in mock_tracer.span_ends
    assert mock_tracer.span_starts["span1"]["inputs"]["text"] == "Hello"
    assert mock_tracer.span_ends["span1"]["outputs"]["response"] == "Hi there"

    # Verify events
    assert len(mock_tracer.events) == 1
    assert mock_tracer.events[0]["name"] == "log"
    assert mock_tracer.events[0]["level"] == "DEBUG"


def test_recording_tracer_context_manager():
    """Test using the RecordingTracerContextManager in tests"""
    with RecordingTracerContextManager() as ctx:
        tracer = ctx.tracer_wrapper

        tracer.initialize("session-id", "test-trace", "user-123")

        with tracer.trace_context("span1", "test-span", {"input": "test"}):
            tracer.set_outputs("span1", {"output": "result"})

        # Verify tracing was recorded
        assert len(ctx.mock_tracer.initialize_calls) == 1
        assert "span1" in ctx.mock_tracer.span_starts
        assert ctx.mock_tracer.span_ends["span1"]["outputs"]["output"] == "result"


@patch("apps.chat.channels.ChannelBase._get_bot_response")
@patch("apps.chat.models.ChatMessage.objects.create")
def test_with_channel_class(mock_create_chat_message, mock_bot_response):
    """Test how to use the mock tracer with the ChannelBase class"""
    from apps.channels.models import ExperimentChannel
    from apps.chat.channels import ChannelBase
    from apps.chat.models import Chat
    from apps.experiments.models import Experiment, ExperimentSession, Participant

    # Setup mocks
    mock_bot_response.return_value = "Mock bot response"
    mock_create_chat_message.return_value = MagicMock()

    # Create mock objects
    mock_experiment = MagicMock(spec=Experiment)
    mock_experiment.name = "Test Experiment"
    mock_experiment.is_public = True
    mock_experiment.conversational_consent_enabled = False

    mock_channel = MagicMock(spec=ExperimentChannel)
    mock_channel.platform = "web"

    mock_participant = MagicMock(spec=Participant)
    mock_participant.identifier = "user-123"

    # Create a proper mock Chat for the session
    mock_chat = MagicMock(spec=Chat)

    mock_session = MagicMock(spec=ExperimentSession)
    mock_session.id = "session-123"
    mock_session.participant = mock_participant
    mock_session.chat = mock_chat  # Add the chat to the session

    # Replace the tracer in ChannelBase with our mock
    with RecordingTracerContextManager() as ctx:
        # Create a subclass that implements the abstract methods
        class TestChannel(ChannelBase):
            voice_replies_supported = False
            supported_message_types = ["text"]

            def send_text_to_user(self, text):
                pass

        # Create an instance
        channel = TestChannel(mock_experiment, mock_channel, mock_session)
        # Replace the tracer with our mock
        channel.tracer = ctx.tracer_wrapper

        # Create a mock message with the required attributes
        message = BaseMessage(
            participant_id="123",
            message_text="Hello bot",
        )

        # Process a message
        channel.new_user_message(message)

        # Verify tracing occurred
        assert len(ctx.mock_tracer.initialize_calls) == 1  # initialize was called
        assert "process_message" in ctx.mock_tracer.span_starts  # process message span was created

        # Check process_message span
        process_span = ctx.mock_tracer.span_starts["process_message"]
        assert process_span["trace_name"] == "process_user_message"
        # The inputs include the message model dump, so check the nested path
        assert process_span["inputs"]["message"]["message_text"] == "Hello bot"


def test_mock_tracer_get_current_trace_info():
    """Test the get_current_trace_info method returns the expected information"""
    mock_tracer = MockTracer()

    # Initialize a trace
    trace_id = uuid4()
    mock_tracer.initialize("test-trace", trace_id, "session-123", "user-456")

    # Now should return trace info
    trace_info = mock_tracer.get_current_trace_info()
    assert trace_info is not None
    assert trace_info.provider_type == "mock"
    assert trace_info.trace_id == trace_id
    assert trace_info.trace_url == f"mock-trace-url/{trace_id}"

    # End the trace
    mock_tracer.end({}, {})

    # Should return None again
    assert mock_tracer.get_current_trace_info() is None
