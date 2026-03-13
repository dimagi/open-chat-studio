import contextlib
from unittest import mock
from unittest.mock import patch

import pytest

from apps.chat.bots import PipelineBot
from apps.pipelines.nodes.base import PipelineState


def test_session_tags():
    session = mock.Mock()
    bot = PipelineBot(session, mock.Mock(), None)
    bot._save_message_to_history = mock.Mock()  # ty: ignore[invalid-assignment]
    bot._save_outputs(
        input_state=PipelineState(messages=["hi"]),
        output=PipelineState(messages=["Hello"], session_tags=[("my-tag", None)]),
    )
    session.chat.create_and_add_tag.assert_called_with("my-tag", session.team, tag_category=None)


def test_save_session_state():
    session = mock.Mock()
    bot = PipelineBot(session, mock.Mock(), None)
    bot._save_message_to_history = mock.Mock()  # ty: ignore[invalid-assignment]
    bot._save_outputs(
        input_state=PipelineState(messages=["hi"]),
        output=PipelineState(messages=["Hello"], session_state={"test": "demo"}),
    )
    assert session.state == {"test": "demo"}
    session.save.assert_called()


def test_save_participant_data():
    session = mock.Mock()
    bot = PipelineBot(session, mock.Mock(), None)
    participant_data = mock.Mock()
    bot._save_message_to_history = mock.Mock()  # ty: ignore[invalid-assignment]
    bot.__dict__["participant_data"] = participant_data
    bot._save_outputs(
        input_state=PipelineState(messages=["hi"]),
        output=PipelineState(messages=["Hello"], participant_data={"test": "demo"}),
    )
    assert participant_data.data == {"test": "demo"}
    participant_data.save.assert_called()


def test_save_participant_data_computes_and_sets_diff():
    session = mock.Mock()
    trace_service = mock.Mock()
    trace_service.get_trace_metadata.return_value = {}
    bot = PipelineBot(session, mock.Mock(), trace_service)
    participant_data = mock.Mock()
    participant_data.data = None
    bot._save_message_to_history = mock.Mock()  # ty: ignore[invalid-assignment]
    bot.__dict__["participant_data"] = participant_data

    input_data = {"name": "Alice", "plan": "free"}
    output_data = {"name": "Alice", "plan": "pro", "score": 100}

    bot._save_outputs(
        input_state=PipelineState(messages=["hi"], participant_data=input_data),
        output=PipelineState(messages=["Hello"], participant_data=output_data),
    )

    assert participant_data.data == output_data
    participant_data.save.assert_called()
    trace_service.set_participant_data_diff.assert_called_once()
    diff = trace_service.set_participant_data_diff.call_args[0][0]
    # Verify the diff captures the actual changes
    assert len(diff) > 0


def test_save_participant_data_no_diff_when_unchanged():
    session = mock.Mock()
    trace_service = mock.Mock()
    trace_service.get_trace_metadata.return_value = {}
    bot = PipelineBot(session, mock.Mock(), trace_service)
    participant_data = mock.Mock()
    bot._save_message_to_history = mock.Mock()  # ty: ignore[invalid-assignment]
    bot.__dict__["participant_data"] = participant_data

    same_data = {"name": "Alice", "plan": "free"}

    bot._save_outputs(
        input_state=PipelineState(messages=["hi"], participant_data=same_data),
        output=PipelineState(messages=["Hello"], participant_data=same_data),
    )

    trace_service.set_participant_data_diff.assert_not_called()


def test_save_participant_data_no_diff_when_no_trace_service():
    session = mock.Mock()
    bot = PipelineBot(session, mock.Mock(), None)
    participant_data = mock.Mock()
    participant_data.data = None
    bot._save_message_to_history = mock.Mock()  # ty: ignore[invalid-assignment]
    bot.__dict__["participant_data"] = participant_data

    bot._save_outputs(
        input_state=PipelineState(messages=["hi"], participant_data={"plan": "free"}),
        output=PipelineState(messages=["Hello"], participant_data={"plan": "pro"}),
    )

    # Should not raise even without trace_service
    assert participant_data.data == {"plan": "pro"}


def test_pipeline_execution_failure_propagates_exception():
    """Pipeline failures re-raise the exception; no direct notification call from bots.py."""
    participant = mock.Mock(identifier="user123", global_data={})
    session = mock.Mock(participant=participant)
    experiment = mock.Mock()
    experiment.name = "Test Experiment"
    experiment.id = 123
    experiment.team = mock.Mock()
    experiment.is_working_version = False

    trace_service = mock.Mock()
    span_mock = mock.Mock()
    trace_service.span.return_value = contextlib.nullcontext(span_mock)

    bot = PipelineBot(session, experiment, trace_service)
    # Bypass the DB-backed cached_property by injecting a mock directly
    bot.__dict__["participant_data"] = mock.Mock(data={})

    with patch("apps.chat.bots.DjangoLangGraphRunner") as mock_runner:
        mock_runner_instance = mock.Mock()
        mock_runner.return_value = mock_runner_instance
        mock_runner_instance.invoke.side_effect = Exception("Pipeline failed")

        with patch("apps.pipelines.graph.PipelineGraph.build_from_pipeline"):
            with pytest.raises(Exception, match="Pipeline failed"):
                bot.process_input(user_input="hello")
