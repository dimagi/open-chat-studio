from langchain.memory.prompt import SUMMARY_PROMPT
from langchain.memory.summary import SummarizerMixin

from apps.chat.models import ChatMessageType
from apps.experiments.models import ExperimentSession


def log(session: ExperimentSession, params):
    last_message = session.chat.messages.last()
    if last_message:
        print(last_message.content)
        return last_message.content


def end_conversation(session: ExperimentSession, params):
    return session.end()


def summarize_conversation(session: ExperimentSession, params):
    try:
        prompt = params["prompt"]
    except KeyError:
        prompt = SUMMARY_PROMPT
    history = session.chat.get_langchain_messages_until_summary()
    current_summary = history.pop(0).content if history[0].type == ChatMessageType.SYSTEM else ""
    messages = session.chat.get_langchain_messages()
    summary = SummarizerMixin(llm=session.experiment.get_chat_model(), prompt=prompt).predict_new_summary(
        messages, current_summary
    )

    return summary
