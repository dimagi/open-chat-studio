from unittest import mock
from unittest.mock import patch

import pytest

from apps.chat.bots import PipelineBot
from apps.ocs_notifications.models import LevelChoices
from apps.pipelines.nodes.base import PipelineState


def test_session_tags():
    session = mock.Mock()
    bot = PipelineBot(session, mock.Mock(), None)
    bot._save_message_to_history = mock.Mock()
    bot._save_outputs(
        input_state=PipelineState(messages=["hi"]),
        output=PipelineState(messages=["Hello"], session_tags=[("my-tag", None)]),
    )
    session.chat.create_and_add_tag.assert_called_with("my-tag", session.team, tag_category=None)


def test_save_session_state():
    session = mock.Mock()
    bot = PipelineBot(session, mock.Mock(), None)
    bot._save_message_to_history = mock.Mock()
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
    bot._save_message_to_history = mock.Mock()
    bot.__dict__["participant_data"] = participant_data
    bot._save_outputs(
        input_state=PipelineState(messages=["hi"]),
        output=PipelineState(messages=["Hello"], participant_data={"test": "demo"}),
    )
    assert participant_data.data == {"test": "demo"}
    participant_data.save.assert_called()


@patch("apps.chat.bots.create_notification")
def test_pipeline_execution_failure_creates_notification(mock_create_notification):
    """Test that pipeline execution exception triggers a notification."""
    # Set up mocks
    session = mock.Mock()
    experiment = mock.Mock()
    experiment.name = "Test Experiment"
    experiment.id = 123
    team = mock.Mock()
    trace_service = mock.Mock()
    experiment.is_working_version = False

    bot = PipelineBot(session, experiment, trace_service)
    bot.team = team

    # Mock the pipeline runner to raise an exception
    with patch("apps.chat.bots.DjangoLangGraphRunner") as mock_runner:
        mock_runner_instance = mock.Mock()
        mock_runner.return_value = mock_runner_instance
        mock_runner_instance.invoke.side_effect = Exception("Pipeline failed")

        with patch("apps.pipelines.graph.PipelineGraph.build_from_pipeline"):
            with pytest.raises(Exception, match="Pipeline failed"):
                bot._run_pipeline(input_state={}, pipeline_to_use=mock.Mock())

    # Verify notification was created with correct parameters
    mock_create_notification.assert_called_once_with(
        title=f"Pipeline execution failed for {experiment}",
        message="Generating a response to the user's message failed due to an error in the pipeline execution",
        level=LevelChoices.ERROR,
        team=team,
        slug="pipeline-execution-failed",
        event_data={"experiment_id": 123, "error": "Pipeline failed"},
        permissions=["experiments.change_experiment"],
    )
