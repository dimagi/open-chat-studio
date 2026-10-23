"""Build sessions with short conversations for the dataset-clone tests."""

from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.models import ExperimentSession
from apps.utils.factories.experiment import ChatMessageFactory, ExperimentSessionFactory


def make_sessions(count, experiment, channel, messages_per_session=2) -> list[ExperimentSession]:
    """Create `count` sessions sharing one experiment/channel, each with a short conversation."""
    sessions = []
    chat_messages = []
    for session_index in range(count):
        session = ExperimentSessionFactory.create(experiment=experiment, experiment_channel=channel)
        chat_messages.extend(
            ChatMessage(
                chat=session.chat,
                message_type=ChatMessageType.HUMAN if turn % 2 == 0 else ChatMessageType.AI,
                content=f"session-{session_index} turn-{turn}",
            )
            for turn in range(messages_per_session)
        )
        sessions.append(session)
    ChatMessage.objects.bulk_create(chat_messages)
    return sessions


def session_with_messages(team=None, experiment=None) -> ExperimentSession:
    kwargs = {}
    if team is not None:
        kwargs["team"] = team
    if experiment is not None:
        kwargs["experiment"] = experiment
    session = ExperimentSessionFactory.create(**kwargs)
    ChatMessageFactory.create(message_type=ChatMessageType.HUMAN, content="Hello", chat=session.chat)
    ChatMessageFactory.create(message_type=ChatMessageType.AI, content="Hi!", chat=session.chat)
    return session
