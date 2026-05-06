from celery.result import AsyncResult
from celery_progress.backend import Progress

from apps.chat.models import ChatMessage, ChatMessageType

DEFAULT_ERROR_MESSAGE = (
    "Sorry something went wrong. This was likely an intermittent error related to load."
    "Please try again, and wait a few minutes if this keeps happening."
)

CUSTOM_ERROR_MESSAGE = (
    "The chatbot is currently unavailable. We are working hard to resolve the issue as quickly"
    " as possible and apologize for any inconvenience. Thank you for your patience."
)


def _resolve_error_details(error: str, user_facing: bool, debug_mode: bool) -> dict:
    if user_facing:
        return {"error_msg": error, "user_facing_error": True}
    if not debug_mode:
        error = CUSTOM_ERROR_MESSAGE if "Invalid parameter" in error else DEFAULT_ERROR_MESSAGE  # TODO: temporary
    return {"error_msg": error, "user_facing_error": False}


def _handle_success_result(result: dict, experiment) -> dict:
    details = {}
    if message_id := result.get("message_id"):
        details["message"] = ChatMessage.objects.get(id=message_id)
    elif response := result.get("response"):
        details["message"] = ChatMessage(content=response, message_type=ChatMessageType.AI)
    if error := result.get("error"):
        details.update(
            _resolve_error_details(error, bool(result.get("user_facing_error")), experiment.debug_mode_enabled)
        )
    return details


def get_message_task_response(experiment, task_id: str):
    progress = Progress(AsyncResult(task_id)).get_info()
    is_complete = progress["complete"]
    is_success = progress["success"]
    skip_render = is_complete and is_success and not progress["result"]
    if skip_render:
        return {}

    message_details = {"message": None, "error_msg": False, "complete": is_complete, "attachments": []}
    if is_complete and is_success:
        message_details.update(_handle_success_result(progress["result"], experiment))
    elif is_complete:
        message_details["error_msg"] = DEFAULT_ERROR_MESSAGE

    message = message_details.get("message")
    if isinstance(message, ChatMessage):
        message_details["attachments"] = message.get_attached_files()

    return message_details
