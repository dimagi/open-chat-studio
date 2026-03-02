from unittest.mock import MagicMock
from uuid import UUID

import pytest

from apps.service_providers.tests.mock_tracer import MockTracer
from apps.service_providers.tracing.service import TracingService


class TestTracingService:
    @pytest.fixture()
    def mock_tracer(self):
        return MockTracer()

    @pytest.fixture()
    def tracing_service(self, mock_tracer):
        return TracingService([mock_tracer], 1, 1)

    @pytest.fixture()
    def empty_tracing_service(self):
        return TracingService.empty()

    @pytest.fixture()
    def mock_session(self):
        participant_mock = MagicMock(identifier="participant_id")
        mock = MagicMock()
        mock.id = "session_id"
        mock.external_id = "session_ext_id"
        mock.__str__.return_value = "session_id"
        mock.participant = participant_mock
        return mock

    def test_initialization(self, mock_tracer):
        service = TracingService([mock_tracer], 1, 1)
        assert service._tracers == [mock_tracer]
        assert not service.activated
        assert service.trace_name is None
        assert service.trace_id is None
        assert service.session is None
        assert service.span_stack == []

    def test_empty_initialization(self):
        service = TracingService.empty()
        assert service._tracers == []
        assert not service.activated

    def test_trace_context_manager(self, tracing_service, mock_tracer, mock_session):
        trace_name = "test_trace"

        with tracing_service.trace(trace_name, session=mock_session, inputs={"input": "test"}) as trace_ctx:
            assert tracing_service.activated
            assert tracing_service.trace_name == trace_name
            assert tracing_service.session == mock_session

            assert isinstance(tracing_service.trace_id, UUID)
            assert mock_tracer.trace_result is not None
            assert mock_tracer.trace_result["name"] == trace_name
            assert mock_tracer.trace_result["session_id"] == mock_session.id
            assert mock_tracer.trace_result["user_id"] == mock_session.participant.identifier
            assert mock_tracer.trace_result["inputs"] == {"input": "test"}

            # Set outputs via context object
            trace_ctx.set_outputs({"output": "test"})
            assert trace_ctx.outputs == {"output": "test"}

        assert not tracing_service.activated

    def test_end_traces(self, tracing_service, mock_tracer, mock_session):
        trace_name = "test_trace"
        with tracing_service.trace(trace_name, mock_session) as trace_ctx:
            # Add some data to verify reset
            trace_ctx.set_outputs({"output": "test"})

        assert mock_tracer.trace_result["ended"]

        assert not tracing_service.activated
        assert not tracing_service.trace_id
        assert not tracing_service.trace_name
        assert not tracing_service.session
        assert not tracing_service.outputs
        assert not tracing_service.span_stack

    def test_span_context_manager(self, tracing_service, mock_tracer, mock_session):
        trace_name = "test_trace"
        span_name = "test_span"
        inputs = {"input": "test"}
        metadata = {"meta": "test"}

        with tracing_service.trace(trace_name, mock_session):
            with tracing_service.span(span_name, inputs, metadata) as span_ctx:
                assert len(mock_tracer.spans) == 1
                span_id = list(mock_tracer.spans.keys())[0]
                span_data = mock_tracer.spans[span_id]
                assert span_data["name"] == span_name
                assert span_data["inputs"] == inputs
                assert span_data["metadata"] == metadata

                outputs = {"output": "test"}
                span_ctx.set_outputs(outputs)
                assert span_ctx.outputs == outputs

            span_data = mock_tracer.spans[span_id]
            assert span_data["ended"]
            assert "outputs" in span_data
            assert span_data["outputs"] == outputs

    def test_span_with_exception(self, tracing_service, mock_tracer, mock_session):
        trace_name = "test_trace"
        span_name = "test_span"
        inputs = {"input": "test"}

        with tracing_service.trace(trace_name, mock_session):
            try:
                with tracing_service.span(span_name, inputs):
                    raise ValueError("Test error")
            except ValueError:
                pass

            # Verify error was recorded
            span_id = list(mock_tracer.spans.keys())[0]
            span_data = mock_tracer.spans[span_id]
            assert span_data["ended"]
            assert span_data["error"] == "Test error"

    def test_span_without_activated_service(self, empty_tracing_service):
        span_name = "test_span"
        inputs = {"input": "test"}

        # Should not raise an exception
        with empty_tracing_service.span(span_name, inputs):
            assert not empty_tracing_service.activated

    def test_get_langchain_callbacks(self, tracing_service, mock_tracer, mock_session):
        # Test with activated service
        with tracing_service.trace("test", mock_session):
            callbacks = tracing_service.get_langchain_callbacks()
            assert len(callbacks) == 1

        assert not tracing_service.activated
        callbacks = tracing_service.get_langchain_callbacks()
        assert callbacks == []

    def test_get_langchain_config(self, tracing_service, mock_tracer, mock_session):
        with tracing_service.trace("test_trace", mock_session):
            config = tracing_service.get_langchain_config()
            assert config["run_name"] == "test_trace run"
            assert len(config["callbacks"]) == 1
            assert config["metadata"]["participant-id"] == "participant_id"
            assert config["metadata"]["session-id"] == "session_ext_id"

            # Test with additional callbacks and configurable
            extra_callback = MagicMock()
            configurable = {"key": "value"}
            config = tracing_service.get_langchain_config(callbacks=[extra_callback], configurable=configurable)
            assert len(config["callbacks"]) == 2
            assert config["configurable"] == configurable

            # Test with a span
            with tracing_service.span("test_span", {"input": "test"}):
                config = tracing_service.get_langchain_config()
                assert config["run_name"] == "test_span run"
                assert len(config["callbacks"]) == 1
                assert config["metadata"]["participant-id"] == "participant_id"
                assert config["metadata"]["session-id"] == "session_ext_id"

        assert not tracing_service.activated
        config = tracing_service.get_langchain_config()
        assert config == {
            "callbacks": [],
            "metadata": {},
            "run_name": "OCS run",
        }

    def test_get_trace_metadata(self, tracing_service, mock_tracer, mock_session):
        with tracing_service.trace("test", mock_session):
            metadata = tracing_service.get_trace_metadata()
            assert metadata == {"trace_info": [{"trace_id": str(mock_tracer.trace_result["id"])}]}

        assert not tracing_service.activated
        metadata = tracing_service.get_trace_metadata()
        assert metadata == {}

    def test_get_trace_metadata_with_exception(self, tracing_service, mock_tracer, mock_session):
        mock_tracer.get_trace_metadata = MagicMock(side_effect=Exception("Test error"))

        with tracing_service.trace("test", mock_session):
            metadata = tracing_service.get_trace_metadata()
            # When an exception occurs, the method continues but doesn't add anything to trace_info
            assert metadata == {}

    def test_active_tracers(self, tracing_service, mock_tracer):
        # Test with no active tracers
        mock_tracer._trace_data = None
        assert tracing_service._active_tracers == []

        # Test with an active tracer
        mock_tracer._trace_data = {"name": "test"}
        assert tracing_service._active_tracers == [mock_tracer]

    def test_add_add_output_message_tags_to_trace(self, tracing_service, mock_tracer, mock_session):
        trace_name = "test_trace"

        with tracing_service.trace(trace_name, mock_session):
            raw_tags = [("tag1", "categoryA"), ("tag2", "categoryB")]
            flat_tags = [f"{category}:{tag}" for tag, category in raw_tags]
            tracing_service.add_output_message_tags_to_trace(flat_tags)
            assert mock_tracer.tags == flat_tags

    def test_tracing_service_raises_error_when_ids_none_and_tracers_nonempty(mock_tracer):
        with pytest.raises(ValueError, match="Tracers must be empty if experiment_id or team_id is None"):
            TracingService(tracers=[mock_tracer], experiment_id=None, team_id=None)  # ty: ignore[invalid-argument-type]
