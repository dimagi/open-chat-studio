from apps.experiments.models import Experiment, ExperimentSession

from .models import LevelChoices
from .utils import create_notification


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
    )


def pipeline_execution_failure_notification(experiment, participant_identifier: str, error: Exception) -> None:
    """Create notification when pipeline execution fails."""
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
    )


def custom_action_api_failure_notification(custom_action, function_def, exception: Exception) -> None:
    """Create notification for API failures."""
    method = function_def.method.upper()
    operation = function_def.name
    create_notification(
        title=f"Custom Action '{custom_action.name}' failed",
        message=f"{method} '{operation}' API call failed: {exception}",
        level=LevelChoices.ERROR,
        team=custom_action.team,
        permissions=["custom_actions.view_customaction"],
        slug="custom-action-api-failure",
        event_data={"action_id": custom_action.id, "exception_type": type(exception).__name__},
    )


def custom_action_unexpected_error_notification(custom_action, function_def, exception: Exception) -> None:
    """Create notification for unexpected errors."""
    method = function_def.method.upper()
    operation = function_def.name

    create_notification(
        title=f"Custom Action '{custom_action.name}' encountered an error",
        message=f"{method} '{operation}' failed with an unexpected error: {exception}",
        level=LevelChoices.ERROR,
        team=custom_action.team,
        permissions=["custom_actions.view_customaction"],
        slug="custom-action-unexpected-error",
        event_data={"action_id": custom_action.id, "exception_type": type(exception).__name__},
    )


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
    )


def audio_synthesis_failure_notification(experiment) -> None:
    """Create notification when audio synthesis fails."""
    create_notification(
        title="Audio Synthesis Failed",
        message="An error occurred while synthesizing a voice response",
        level=LevelChoices.ERROR,
        slug="audio-synthesis-failed",
        team=experiment.team,
        permissions=["experiments.view_experimentsession"],
        event_data={"bot_id": experiment.id},
    )


def file_delivery_failure_notification(experiment, platform: str, platform_title: str, content_type: str) -> None:
    """Create notification when file delivery to user fails."""
    create_notification(
        title="Message Delivery Failed",
        message=f"An error occurred while delivering a file attachment to the user via {platform_title}",
        level=LevelChoices.ERROR,
        slug="file-delivery-failed",
        team=experiment.team,
        permissions=["experiments.view_experimentsession"],
        event_data={
            "bot_id": experiment.id,
            "platform": platform,
            "content_type": content_type,
        },
    )


def audio_transcription_failure_notification(experiment, platform: str) -> None:
    """Create notification when audio transcription fails."""
    create_notification(
        title="Audio Transcription Failed",
        message="An error occurred while transcribing a voice message",
        level=LevelChoices.ERROR,
        slug="audio-transcription-failed",
        team=experiment.team,
        permissions=["experiments.view_experimentsession"],
        event_data={"bot_id": experiment.id, "platform": platform},
    )


def message_delivery_failure_notification(experiment, platform: str, platform_title: str, context: str) -> None:
    """Create notification when message delivery fails."""
    create_notification(
        title=f"Message Delivery Failed for {experiment.name}",
        message=f"An error occurred while delivering a {context} to the user via {platform_title}",
        level=LevelChoices.ERROR,
        slug="message-delivery-failed",
        team=experiment.team,
        permissions=["experiments.view_experimentsession"],
        event_data={
            "bot_id": experiment.id,
            "platform": platform,
            "context": context,
        },
    )


def tool_error_notification(team, tool_name: str, error_message: str) -> None:
    """Create notification when a tool execution fails."""
    create_notification(
        title="Tool Error Detected",
        message=error_message,
        level=LevelChoices.ERROR,
        team=team,
        slug="tool-error",
        event_data={"tool_name": tool_name, "error_message": error_message},
    )
