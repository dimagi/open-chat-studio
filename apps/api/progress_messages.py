import logging

from django.core.cache import cache

from apps.help.agents.progress_messages import ProgressMessagesAgent, ProgressMessagesInput

logger = logging.getLogger("ocs.api_chat")

PROGRESS_MESSAGES_TTL = 24 * 3600
GENERATION_LOCK_TIMEOUT = 60


def progress_messages_key(session_id) -> str:
    return f"progress_messages:{session_id}"


def get_progress_message(session_id, chatbot_name, chatbot_description, throttle_key=None) -> str | None:
    """Return the next cached progress message for the session, cycling back to the start of the list.

    On a cache miss this queues generation in the background and returns None.
    If throttle_key is provided, a new message is only returned once every 5 seconds.
    Within the 5-second window the same message is returned.
    """
    last_key = f"progress_last:{throttle_key}" if throttle_key else None
    if last_key:
        last = cache.get(last_key)
        if last:
            return last

    key = progress_messages_key(session_id)
    messages = cache.get(key)
    if not messages:
        _queue_generation(session_id, chatbot_name, chatbot_description)
        return None
    cache.touch(key, PROGRESS_MESSAGES_TTL)

    index_key = f"progress_index:{session_id}"
    index = cache.get(index_key, 0)
    message = messages[index % len(messages)]
    cache.set(index_key, index + 1, PROGRESS_MESSAGES_TTL)

    if last_key:
        cache.set(last_key, message, 5)

    return message


def _queue_generation(session_id, chatbot_name, chatbot_description):
    """Queue progress message generation unless it was queued for this session in the last minute."""
    from apps.api.tasks import generate_progress_messages_task  # noqa: PLC0415 - circular import

    if cache.add(f"progress_generating:{session_id}", 1, timeout=GENERATION_LOCK_TIMEOUT):
        generate_progress_messages_task.delay(
            session_id=str(session_id), chatbot_name=chatbot_name, chatbot_description=chatbot_description
        )


def get_progress_messages(chatbot_name, chatbot_description) -> list[str]:
    try:
        agent = ProgressMessagesAgent(
            input=ProgressMessagesInput(chatbot_name=chatbot_name, chatbot_description=chatbot_description)
        )
        return agent.run().messages
    except Exception:
        logger.exception("Failed to generate progress messages for chatbot '%s'", chatbot_name)
        return []
