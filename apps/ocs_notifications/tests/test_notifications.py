from unittest.mock import Mock, patch

import pytest

from apps.ocs_notifications.models import LevelChoices
from apps.ocs_notifications.notifications import (
    audio_synthesis_failure_notification,
    audio_transcription_failure_notification,
    custom_action_api_failure_notification,
    custom_action_health_check_failure_notification,
    custom_action_unexpected_error_notification,
    file_delivery_failure_notification,
    llm_error_notification,
    message_delivery_failure_notification,
    pipeline_execution_failure_notification,
    tool_error_notification,
)
from apps.utils.factories.custom_actions import CustomActionFactory
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.team import TeamFactory


class TestCustomActionHealthCheckFailureNotification:
    @pytest.mark.django_db()
    @patch("apps.ocs_notifications.notifications.create_notification")
    def test_creates_notification(self, mock_create_notification):
        # Arrange
        action = CustomActionFactory.create()
        failure_reason = "Connection timeout"

        # Act
        custom_action_health_check_failure_notification(action, failure_reason)

        # Assert
        mock_create_notification.assert_called_once_with(
            title="Custom Action is down",
            message=f"The custom action '{action.name}' health check failed: {failure_reason}.",
            level=LevelChoices.ERROR,
            team=action.team,
            slug="custom-action-health-check",
            event_data={"action_id": action.id, "status": action.health_status},
            permissions=["custom_actions.change_customaction"],
            links={"View Action": action.get_absolute_url()},
        )


class TestPipelineExecutionFailureNotification:
    @pytest.mark.django_db()
    @patch("apps.ocs_notifications.notifications.create_notification")
    def test_creates_notification(self, mock_create_notification):
        # Arrange
        experiment = ExperimentFactory.create()
        session = ExperimentSessionFactory.create(experiment=experiment)
        error = Exception("Pipeline error")

        # Act
        pipeline_execution_failure_notification(experiment, session, error)

        # Assert
        expected_message = (
            f"Generating a response for user '{session.participant.identifier}' failed due to an error in the pipeline "
            "execution"
        )
        mock_create_notification.assert_called_once_with(
            title=f"Pipeline execution failed for {experiment}",
            message=expected_message,
            level=LevelChoices.ERROR,
            team=experiment.team,
            slug="pipeline-execution-failed",
            event_data={"experiment_id": experiment.id, "error": str(error)},
            permissions=["experiments.change_experiment"],
            links={"View Bot": experiment.get_absolute_url(), "View Session": session.get_absolute_url()},
        )


class TestCustomActionApiFailureNotification:
    @pytest.mark.django_db()
    @patch("apps.ocs_notifications.notifications.create_notification")
    def test_creates_notification(self, mock_create_notification):
        # Arrange
        custom_action = CustomActionFactory.create()
        function_def = Mock()
        function_def.method = "post"
        function_def.name = "create_user"
        exception = Exception("API call failed")

        # Act
        custom_action_api_failure_notification(custom_action, function_def, exception)

        # Assert
        mock_create_notification.assert_called_once_with(
            title=f"Custom Action '{custom_action.name}' failed",
            message=f"POST 'create_user' API call failed: {exception}",
            level=LevelChoices.ERROR,
            team=custom_action.team,
            permissions=["custom_actions.view_customaction"],
            slug="custom-action-api-failure",
            event_data={"action_id": custom_action.id, "exception_type": "Exception"},
            links={"View Action": custom_action.get_absolute_url()},
        )


class TestCustomActionUnexpectedErrorNotification:
    @pytest.mark.django_db()
    @patch("apps.ocs_notifications.notifications.create_notification")
    def test_creates_notification(self, mock_create_notification):
        # Arrange
        custom_action = CustomActionFactory.create()
        function_def = Mock()
        function_def.method = "put"
        function_def.name = "update_user"
        exception = RuntimeError("Unexpected error")

        # Act
        custom_action_unexpected_error_notification(custom_action, function_def, exception)

        # Assert
        mock_create_notification.assert_called_once_with(
            title=f"Custom Action '{custom_action.name}' encountered an error",
            message=f"PUT 'update_user' failed with an unexpected error: {exception}",
            level=LevelChoices.ERROR,
            team=custom_action.team,
            permissions=["custom_actions.view_customaction"],
            slug="custom-action-unexpected-error",
            event_data={"action_id": custom_action.id, "exception_type": "RuntimeError"},
            links={"View Action": custom_action.get_absolute_url()},
        )


class TestLlmErrorNotification:
    @pytest.mark.django_db()
    @patch("apps.ocs_notifications.notifications.create_notification")
    def test_creates_notification(self, mock_create_notification):
        # Arrange
        experiment = ExperimentFactory.create()
        session = ExperimentSessionFactory.create(experiment=experiment)
        error_message = "Token limit exceeded"

        # Act
        llm_error_notification(experiment, session, error_message)

        # Assert
        expected_message = f"An LLM error occurred for participant '{session.participant.identifier}': {error_message}"
        mock_create_notification.assert_called_once_with(
            title=f"LLM Error Detected for '{experiment}'",
            message=expected_message,
            level=LevelChoices.ERROR,
            team=experiment.team,
            slug="llm-error",
            event_data={"bot_id": experiment.id, "error_message": error_message},
            permissions=["experiments.change_experiment"],
            links={"View Bot": experiment.get_absolute_url(), "View Session": session.get_absolute_url()},
        )


class TestAudioSynthesisFailureNotification:
    @pytest.mark.django_db()
    @patch("apps.ocs_notifications.notifications.create_notification")
    def test_creates_notification_with_session(self, mock_create_notification):
        # Arrange
        experiment = ExperimentFactory.create()
        session = ExperimentSessionFactory.create(experiment=experiment)

        # Act
        audio_synthesis_failure_notification(experiment, session)

        # Assert
        expected_links = {
            "View Bot": experiment.get_absolute_url(),
            "View Session": session.get_absolute_url(),
        }
        mock_create_notification.assert_called_once_with(
            title="Audio Synthesis Failed",
            message=f"An error occurred while synthesizing a voice response for '{experiment.name}'",
            level=LevelChoices.ERROR,
            slug="audio-synthesis-failed",
            team=experiment.team,
            permissions=["experiments.view_experimentsession"],
            event_data={"bot_id": experiment.id},
            links=expected_links,
        )

    @pytest.mark.django_db()
    @patch("apps.ocs_notifications.notifications.create_notification")
    def test_creates_notification_without_session(self, mock_create_notification):
        # Arrange
        experiment = ExperimentFactory.create()

        # Act
        audio_synthesis_failure_notification(experiment)

        # Assert
        expected_links = {"View Bot": experiment.get_absolute_url()}
        mock_create_notification.assert_called_once_with(
            title="Audio Synthesis Failed",
            message=f"An error occurred while synthesizing a voice response for '{experiment.name}'",
            level=LevelChoices.ERROR,
            slug="audio-synthesis-failed",
            team=experiment.team,
            permissions=["experiments.view_experimentsession"],
            event_data={"bot_id": experiment.id},
            links=expected_links,
        )


class TestFileDeliveryFailureNotification:
    @pytest.mark.django_db()
    @patch("apps.ocs_notifications.notifications.create_notification")
    def test_creates_notification(self, mock_create_notification):
        # Arrange
        experiment = ExperimentFactory.create()
        session = ExperimentSessionFactory.create(experiment=experiment)
        platform_title = "WhatsApp"
        content_type = "image/png"

        # Act
        file_delivery_failure_notification(experiment, platform_title, content_type, session)

        # Assert
        expected_message = (
            f"An error occurred while delivering a file attachment to the user via "
            f"{platform_title} for '{experiment.name}'"
        )
        expected_links = {
            "View Bot": experiment.get_absolute_url(),
            "View Session": session.get_absolute_url(),
        }
        mock_create_notification.assert_called_once_with(
            title="File Delivery Failed",
            message=expected_message,
            level=LevelChoices.ERROR,
            slug="file-delivery-failed",
            team=experiment.team,
            permissions=["experiments.view_experimentsession"],
            event_data={
                "bot_id": experiment.id,
                "platform": platform_title,
                "content_type": content_type,
            },
            links=expected_links,
        )


class TestAudioTranscriptionFailureNotification:
    @pytest.mark.django_db()
    @patch("apps.ocs_notifications.notifications.create_notification")
    def test_creates_notification(self, mock_create_notification):
        # Arrange
        experiment = ExperimentFactory.create()

        # Act
        audio_transcription_failure_notification(experiment, "WhatsApp")

        # Assert
        mock_create_notification.assert_called_once_with(
            title="Audio Transcription Failed",
            message=f"An error occurred while transcribing a voice message for '{experiment.name}'",
            level=LevelChoices.ERROR,
            slug="audio-transcription-failed",
            team=experiment.team,
            permissions=["experiments.view_experimentsession"],
            event_data={"bot_id": experiment.id, "platform": "WhatsApp"},
            links={"View Bot": experiment.get_absolute_url()},
        )


class TestMessageDeliveryFailureNotification:
    @pytest.mark.django_db()
    @patch("apps.ocs_notifications.notifications.create_notification")
    def test_creates_notification(self, mock_create_notification):
        # Arrange
        experiment = ExperimentFactory.create()
        session = ExperimentSessionFactory.create(experiment=experiment)
        platform_title = "Slack"
        context = "message"

        # Act
        message_delivery_failure_notification(experiment, session, platform_title, context)

        # Assert
        expected_message = (
            f"An error occurred while delivering a {context} to {session.participant.identifier} via {platform_title}"
        )
        mock_create_notification.assert_called_once_with(
            title=f"Message Delivery Failed for {experiment.name}",
            message=expected_message,
            level=LevelChoices.ERROR,
            slug="message-delivery-failed",
            team=experiment.team,
            permissions=["experiments.view_experimentsession"],
            event_data={
                "bot_id": experiment.id,
                "platform": platform_title,
                "context": context,
            },
            links={"View Bot": experiment.get_absolute_url(), "View Session": session.get_absolute_url()},
        )


class TestToolErrorNotification:
    @pytest.mark.django_db()
    @patch("apps.ocs_notifications.notifications.create_notification")
    def test_creates_notification_with_session(self, mock_create_notification):
        # Arrange
        team = TeamFactory.create()
        tool_name = "weather_api"
        error_message = "API rate limit exceeded"
        session = ExperimentSessionFactory.create()

        # Act
        tool_error_notification(team, tool_name, error_message, session)

        # Assert
        expected_event_data = {"tool_name": tool_name, "error_message": error_message}
        expected_links = {
            "View Bot": session.experiment.get_absolute_url(),
            "View Session": session.get_absolute_url(),
        }
        mock_create_notification.assert_called_once_with(
            title="Tool Error Detected",
            message=error_message,
            level=LevelChoices.ERROR,
            team=team,
            slug="tool-error",
            event_data=expected_event_data,
            permissions=None,
            links=expected_links,
        )

    @pytest.mark.django_db()
    @patch("apps.ocs_notifications.notifications.create_notification")
    def test_creates_notification_without_session(self, mock_create_notification):
        # Arrange
        team = TeamFactory.create()
        tool_name = "database_connector"
        error_message = "Connection timeout"

        # Act
        tool_error_notification(team, tool_name, error_message)

        # Assert
        expected_event_data = {"tool_name": tool_name, "error_message": error_message}
        expected_links = {}
        mock_create_notification.assert_called_once_with(
            title="Tool Error Detected",
            message=error_message,
            level=LevelChoices.ERROR,
            team=team,
            slug="tool-error",
            event_data=expected_event_data,
            permissions=None,
            links=expected_links,
        )

    @pytest.mark.django_db()
    @patch("apps.ocs_notifications.notifications.create_notification")
    def test_handles_none_session(self, mock_create_notification):
        # Arrange
        team = TeamFactory.create()
        tool_name = "test_tool"
        error_message = "Test error"

        # Act
        tool_error_notification(team, tool_name, error_message, session=None)

        # Assert
        call_args = mock_create_notification.call_args[1]
        assert call_args["links"] == {}


class TestTraceErrorNotification:
    @pytest.mark.django_db()
    @patch("apps.ocs_notifications.notifications.create_notification")
    def test_creates_notification_with_trace_url(self, mock_create_notification):
        """trace_error_notification passes correct slug, title, links to create_notification."""
        # Local import is temporary: trace_error_notification does not exist yet (TDD red phase).
        # Move to module-level imports in Task 5 once the function is implemented.
        from apps.ocs_notifications.notifications import trace_error_notification

        experiment = ExperimentFactory.create()
        session = ExperimentSessionFactory.create(experiment=experiment)
        trace_url = "/traces/team/42/"

        trace_error_notification(
            experiment=experiment,
            session=session,
            span_name="Run Pipeline",
            error_message="Something went wrong",
            permissions=["experiments.change_experiment"],
            trace_url=trace_url,
        )

        mock_create_notification.assert_called_once_with(
            title=f"Run Pipeline Failed for '{experiment}'",
            message=(
                f"An error occurred during 'Run Pipeline' for participant "
                f"'{session.participant.identifier}': Something went wrong"
            ),
            level=LevelChoices.ERROR,
            team=experiment.team,
            slug="run-pipeline",
            event_data={"experiment_id": experiment.id, "span_name": "Run Pipeline"},
            permissions=["experiments.change_experiment"],
            links={
                "View Bot": experiment.get_absolute_url(),
                "View Session": session.get_absolute_url(),
                "View Trace": trace_url,
            },
        )

    @pytest.mark.django_db()
    @patch("apps.ocs_notifications.notifications.create_notification")
    def test_omits_view_trace_link_when_trace_url_is_none(self, mock_create_notification):
        """trace_error_notification omits 'View Trace' link when trace_url is None."""
        # Local import is temporary: trace_error_notification does not exist yet (TDD red phase).
        # Move to module-level imports in Task 5 once the function is implemented.
        from apps.ocs_notifications.notifications import trace_error_notification

        experiment = ExperimentFactory.create()
        session = ExperimentSessionFactory.create(experiment=experiment)

        trace_error_notification(
            experiment=experiment,
            session=session,
            span_name="Run Pipeline",
            error_message="Something went wrong",
            permissions=["experiments.change_experiment"],
            trace_url=None,
        )

        call_kwargs = mock_create_notification.call_args.kwargs
        assert "View Trace" not in call_kwargs["links"]

    @pytest.mark.django_db()
    @patch("apps.ocs_notifications.notifications.create_notification")
    def test_slug_derived_from_span_name(self, mock_create_notification):
        """Slug is computed via slugify from span_name."""
        # Local import is temporary: trace_error_notification does not exist yet (TDD red phase).
        # Move to module-level imports in Task 5 once the function is implemented.
        from apps.ocs_notifications.notifications import trace_error_notification

        experiment = ExperimentFactory.create()
        session = ExperimentSessionFactory.create(experiment=experiment)

        trace_error_notification(
            experiment=experiment,
            session=session,
            span_name="seed_message",
            error_message="err",
            permissions=None,
            trace_url=None,
        )

        call_kwargs = mock_create_notification.call_args.kwargs
        assert call_kwargs["slug"] == "seed-message"
        assert call_kwargs["title"] == f"Seed Message Failed for '{experiment}'"
