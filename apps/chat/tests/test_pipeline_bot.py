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
