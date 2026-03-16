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


class TestPersistPipelineState:
    """Tests for _persist_pipeline_state, which saves state changes without saving messages to history."""

    def test_persists_session_tags(self):
        session = mock.Mock()
        bot = PipelineBot(session, mock.Mock(), None)
        bot._persist_pipeline_state(
            input_state=PipelineState(messages=["hi"]),
            output=PipelineState(messages=["Hello"], session_tags=[("my-tag", None)]),
        )
        session.chat.create_and_add_tag.assert_called_with("my-tag", session.team, tag_category=None)

    def test_persists_session_state(self):
        session = mock.Mock()
        bot = PipelineBot(session, mock.Mock(), None)
        bot._persist_pipeline_state(
            input_state=PipelineState(messages=["hi"]),
            output=PipelineState(messages=["Hello"], session_state={"test": "demo"}),
        )
        assert session.state == {"test": "demo"}
        session.save.assert_called_with(update_fields=["state"])

    def test_persists_participant_data(self):
        session = mock.Mock()
        bot = PipelineBot(session, mock.Mock(), None)
        participant_data = mock.Mock()
        bot.__dict__["participant_data"] = participant_data
        bot._persist_pipeline_state(
            input_state=PipelineState(messages=["hi"]),
            output=PipelineState(messages=["Hello"], participant_data={"test": "demo"}),
        )
        assert participant_data.data == {"test": "demo"}
        participant_data.save.assert_called()

    def test_no_save_when_state_unchanged(self):
        session = mock.Mock()
        bot = PipelineBot(session, mock.Mock(), None)
        participant_data = mock.Mock()
        bot.__dict__["participant_data"] = participant_data
        same_data = {"key": "value"}
        bot._persist_pipeline_state(
            input_state=PipelineState(messages=["hi"], participant_data=same_data, session_state={"a": 1}),
            output=PipelineState(messages=["Hello"], participant_data=same_data, session_state={"a": 1}),
        )
        participant_data.save.assert_not_called()
        session.save.assert_not_called()
        session.chat.create_and_add_tag.assert_not_called()


class TestInvokePipelineWithoutHistory:
    """Tests that invoke_pipeline with save_run_to_history=False still persists state changes."""

    def _make_bot(self, session=None):
        session = session or mock.Mock()
        bot = PipelineBot(session, mock.Mock(), None)
        bot.__dict__["participant_data"] = mock.Mock()
        return bot

    def test_persists_state_when_not_saving_history(self):
        bot = self._make_bot()
        output_state = PipelineState(
            messages=["Hello"],
            participant_data={"new": "data"},
            session_state={"count": 1},
            session_tags=[("event-tag", "")],
        )
        with patch.object(bot, "_run_pipeline", return_value=output_state):
            bot.invoke_pipeline(
                input_state=PipelineState(messages=["hi"]),
                save_run_to_history=False,
                pipeline=mock.Mock(),
            )
        assert bot.session.state == {"count": 1}
        assert bot.__dict__["participant_data"].data == {"new": "data"}
        bot.session.chat.create_and_add_tag.assert_called_with("event-tag", bot.session.team, tag_category="")

    def test_processes_intents_when_not_saving_history(self):
        bot = self._make_bot()
        output_state = PipelineState(messages=["bye"], intents=["end_session"])
        with patch.object(bot, "_run_pipeline", return_value=output_state):
            bot.invoke_pipeline(
                input_state=PipelineState(messages=["hi"]),
                save_run_to_history=False,
                pipeline=mock.Mock(),
            )
        bot.session.end.assert_called_once()

    def test_does_not_save_message_to_history(self):
        bot = self._make_bot()
        bot._save_message_to_history = mock.Mock()
        output_state = PipelineState(messages=["Hello"])
        with patch.object(bot, "_run_pipeline", return_value=output_state):
            result = bot.invoke_pipeline(
                input_state=PipelineState(messages=["hi"]),
                save_run_to_history=False,
                pipeline=mock.Mock(),
            )
        bot._save_message_to_history.assert_not_called()
        assert result.content == output_state


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
