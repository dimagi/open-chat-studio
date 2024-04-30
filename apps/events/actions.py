from langchain.memory.prompt import SUMMARY_PROMPT
from langchain.memory.summary import SummarizerMixin

from apps.chat.models import ChatMessageType
from apps.experiments.models import ExperimentSession


def log(session: ExperimentSession, action) -> str:
    last_message = session.chat.messages.last()
    if last_message:
        return last_message.content


def end_conversation(session: ExperimentSession, action) -> str:
    session.end()
    return "Session ended"


def summarize_conversation(session: ExperimentSession, action) -> str:
    try:
        prompt = action.params["prompt"]
    except KeyError:
        prompt = SUMMARY_PROMPT
    history = session.chat.get_langchain_messages_until_summary()
    current_summary = history.pop(0).content if history[0].type == ChatMessageType.SYSTEM else ""
    messages = session.chat.get_langchain_messages()
    summary = SummarizerMixin(llm=session.experiment.get_chat_model(), prompt=prompt).predict_new_summary(
        messages, current_summary
    )

    return summary


def schedule_trigger(session: ExperimentSession, action) -> str:
    from apps.events.models import ScheduledMessage

    ScheduledMessage.objects.create(participant=session.participant, team=session.team, action=action)
    return ""


def send_message_to_bot(session: ExperimentSession, action) -> str:
    from apps.chat.tasks import bot_prompt_for_user, try_send_message

    try:
        message = action.params["message_to_bot"]
    except KeyError:
        message = "The user hasn't responded, please prompt them again."

    # TODO: experiment_session.send_bot_message
    ping_message = bot_prompt_for_user(session, prompt_instruction=message)
    try_send_message(experiment_session=session, message=ping_message)

    last_message = session.chat.messages.last()
    if last_message:
        return last_message.content
