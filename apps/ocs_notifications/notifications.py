import logging

from apps.experiments.models import Experiment, ExperimentSession
from apps.utils.decorators import silence_exceptions

from .models import LevelChoices
from .utils import create_notification

logger = logging.getLogger("ocs.notifications")


@silence_exceptions(logger, log_message="Failed to create custom action health check failure notification")
def custom_action_health_check_failure_notification(action, failure_reason: str) -> None:
    """Create notification when custom action health check fails."""
    create_notification(
        title="Custom Action is down",
        message=f"The custom action '{action.name}' health check failed: {failure_reason}.",
        level=LevelChoices.ERROR,
        team=action.team,
        slug="custom-action-health-check",
        event_data={"action_id": action.id, "status": action.health_status},
        permissions=["custom_actions.change_customaction"],
        links={"View Action": action.get_absolute_url()},
    )


@silence_exceptions(logger, log_message="Failed to create pipeline execution failure notification")
def pipeline_execution_failure_notification(experiment, session: ExperimentSession, error: Exception) -> None:
    """Create notification when pipeline execution fails."""
    participant_identifier = session.participant.identifier
    create_notification(
        title=f"Pipeline execution failed for {experiment}",
        message=(
            f"Generating a response for user '{participant_identifier}' failed due to an error in the pipeline "
            "execution"
        ),
        level=LevelChoices.ERROR,
        team=experiment.team,
        slug="pipeline-execution-failed",
        event_data={"experiment_id": experiment.id, "error": str(error)},
        permissions=["experiments.change_experiment"],
        links={"View Bot": experiment.get_absolute_url(), "View Session": session.get_absolute_url()},
    )


@silence_exceptions(logger, log_message="Failed to create custom action API failure notification")
def custom_action_api_failure_notification(custom_action, function_def, exception: Exception) -> None:
    """Create notification for API failures."""
    method = function_def.method.upper()
    operation = function_def.name
    create_notification(
        title=f"Custom Action '{custom_action.name}' failed",
        message=f"{method} '{operation}' API call failed: {exception}",
        level=LevelChoices.ERROR,
        team=custom_action.team,
        slug="custom-action-api-failure",
        event_data={"action_id": custom_action.id, "exception_type": type(exception).__name__},
        permissions=["custom_actions.view_customaction"],
        links={"View Action": custom_action.get_absolute_url()},
    )


@silence_exceptions(logger, log_message="Failed to create custom action unexpected error notification")
def custom_action_unexpected_error_notification(custom_action, function_def, exception: Exception) -> None:
    """Create notification for unexpected errors."""
    method = function_def.method.upper()
    operation = function_def.name

    create_notification(
        title=f"Custom Action '{custom_action.name}' encountered an error",
        message=f"{method} '{operation}' failed with an unexpected error: {exception}",
        level=LevelChoices.ERROR,
        team=custom_action.team,
        slug="custom-action-unexpected-error",
        event_data={"action_id": custom_action.id, "exception_type": type(exception).__name__},
        permissions=["custom_actions.view_customaction"],
        links={"View Action": custom_action.get_absolute_url()},
    )


@silence_exceptions(logger, log_message="Failed to create LLM error notification")
def llm_error_notification(experiment_id: int, session_id: int, error_message: str):
    experiment = Experiment.objects.get(id=experiment_id)
    session = ExperimentSession.objects.get(id=session_id)
    message = f"An LLM error occurred for participant '{session.participant.identifier}': {error_message}"
    create_notification(
        title=f"LLM Error Detected for '{experiment.name}'",
        message=message,
        level=LevelChoices.ERROR,
        team=experiment.team,
        slug="llm-error",
        event_data={"bot_id": experiment_id, "error_message": error_message},
        permissions=["experiments.change_experiment"],
        links={"View Bot": experiment.get_absolute_url(), "View Session": session.get_absolute_url()},
    )


@silence_exceptions(logger, log_message="Failed to create audio synthesis failure notification")
def audio_synthesis_failure_notification(experiment, session: ExperimentSession = None) -> None:
    """Create notification when audio synthesis fails."""
    links = {"View Bot": experiment.get_absolute_url()}
    if session:
        links["View Session"] = session.get_absolute_url()

    create_notification(
        title="Audio Synthesis Failed",
        message=f"An error occurred while synthesizing a voice response for '{experiment.name}'",
        level=LevelChoices.ERROR,
        team=experiment.team,
        slug="audio-synthesis-failed",
        event_data={"bot_id": experiment.id},
        permissions=["experiments.view_experimentsession"],
        links=links,
    )


@silence_exceptions(logger, log_message="Failed to create file delivery failure notification")
def file_delivery_failure_notification(
    experiment, platform_title: str, content_type: str, session: ExperimentSession
) -> None:
    """Create notification when file delivery to user fails."""
    links = {"View Bot": experiment.get_absolute_url(), "View Session": session.get_absolute_url()}

    create_notification(
        title="File Delivery Failed",
        message=(
            "An error occurred while delivering a file attachment to the user via "
            f"{platform_title} for '{experiment.name}'"
        ),
        level=LevelChoices.ERROR,
        team=experiment.team,
        slug="file-delivery-failed",
        event_data={
            "bot_id": experiment.id,
            "platform": platform_title,
            "content_type": content_type,
        },
        permissions=["experiments.view_experimentsession"],
        links=links,
    )


@silence_exceptions(logger, log_message="Failed to create audio transcription failure notification")
def audio_transcription_failure_notification(experiment, platform: str) -> None:
    """Create notification when audio transcription fails."""
    create_notification(
        title="Audio Transcription Failed",
        message=f"An error occurred while transcribing a voice message for '{experiment.name}'",
        level=LevelChoices.ERROR,
        team=experiment.team,
        slug="audio-transcription-failed",
        event_data={"bot_id": experiment.id, "platform": platform},
        permissions=["experiments.view_experimentsession"],
        links={"View Bot": experiment.get_absolute_url()},
    )


@silence_exceptions(logger, log_message="Failed to create message delivery failure notification")
def message_delivery_failure_notification(experiment, session, platform_title: str, context: str) -> None:
    """Create notification when message delivery fails."""
    identifier = session.participant.identifier
    create_notification(
        title=f"Message Delivery Failed for {experiment.name}",
        message=f"An error occurred while delivering a {context} to {identifier} via {platform_title}",
        level=LevelChoices.ERROR,
        team=experiment.team,
        slug="message-delivery-failed",
        event_data={
            "bot_id": experiment.id,
            "platform": platform_title,
            "context": context,
        },
        permissions=["experiments.view_experimentsession"],
        links={"View Bot": experiment.get_absolute_url(), "View Session": session.get_absolute_url()},
    )


@silence_exceptions(logger, log_message="Failed to create tool error notification")
def tool_error_notification(team, tool_name: str, error_message: str, session=None) -> None:
    """Create notification when a tool execution fails."""
    event_data = {"tool_name": tool_name, "error_message": error_message}
    links = {}

    if session:
        links["View Bot"] = session.experiment.get_absolute_url()
        links["View Session"] = session.get_absolute_url()

    create_notification(
        title="Tool Error Detected",
        message=error_message,
        level=LevelChoices.ERROR,
        team=team,
        slug="tool-error",
        event_data=event_data,
        permissions=None,
        links=links,
    )
