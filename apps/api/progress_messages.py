import logging

from django.core.cache import cache

from apps.help.agents.progress_messages import ProgressMessagesAgent, ProgressMessagesInput

logger = logging.getLogger("ocs.api_chat")

PROGRESS_MESSAGES_TTL = 24 * 3600


def progress_messages_key(session_id) -> str:
    return f"progress_messages:{session_id}"


def get_progress_message(session_id, throttle_key=None) -> str | None:
    """Return the next cached progress message for the session, cycling back to the start of the list.

    Messages are generated in the background when the session starts, so this returns None until they are cached.
    If throttle_key is provided, a new message is only returned once every 5 seconds.
    Within the 5-second window the same message is returned.
    """
    last_key = f"progress_last:{throttle_key}" if throttle_key else None
    if last_key:
        last = cache.get(last_key)
        if last:
            return last

    messages = cache.get(progress_messages_key(session_id))
    if not messages:
        return None

    index_key = f"progress_index:{session_id}"
    index = cache.get(index_key, 0)
    message = messages[index % len(messages)]
    cache.set(index_key, index + 1, PROGRESS_MESSAGES_TTL)

    if last_key:
        cache.set(last_key, message, 5)

    return message


def get_progress_messages(chatbot_name, chatbot_description) -> list[str]:
    try:
        agent = ProgressMessagesAgent(
            input=ProgressMessagesInput(chatbot_name=chatbot_name, chatbot_description=chatbot_description)
        )
        return agent.run().messages
    except Exception:
        logger.exception("Failed to generate progress messages for chatbot '%s'", chatbot_name)
        return []
