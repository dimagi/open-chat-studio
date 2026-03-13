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


def get_message_task_response(experiment, task_id: str):
    progress = Progress(AsyncResult(task_id)).get_info()
    is_complete = progress["complete"]
    is_success = progress["success"]
    skip_render = is_complete and is_success and not progress["result"]
    if skip_render:
        return {}

    message_details = {"message": None, "error_msg": False, "complete": is_complete, "attachments": []}
    if is_complete and is_success:
        result = progress["result"]
        if message_id := result.get("message_id"):
            message_details["message"] = ChatMessage.objects.get(id=message_id)
        elif response := result.get("response"):
            message_details["message"] = ChatMessage(content=response, message_type=ChatMessageType.AI)
        if error := result.get("error"):
            if not experiment.debug_mode_enabled:
                if "Invalid parameter" in error:  # TODO: temporary
                    message_details["error_msg"] = CUSTOM_ERROR_MESSAGE
                else:
                    message_details["error_msg"] = DEFAULT_ERROR_MESSAGE
            else:
                message_details["error_msg"] = error
    elif is_complete:
        message_details["error_msg"] = DEFAULT_ERROR_MESSAGE

    message = message_details.get("message")
    if isinstance(message, ChatMessage):
        message_details["attachments"] = message.get_attached_files()

    return message_details
