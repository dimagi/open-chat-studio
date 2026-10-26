"""Build sessions with short conversations for the dataset-clone tests."""

from apps.chat.models import ChatMessage, ChatMessageType
from apps.evaluations.evaluation_messages import iter_message_evaluation_messages_for_sessions
from apps.evaluations.models import EvaluationDataset, EvaluationMessage, EvaluationMode
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


def pairs_for_sessions(team, external_ids, filter_params=None, timezone=None) -> list[EvaluationMessage]:
    """Build the team's pair messages for these sessions, in session pk order."""
    return list(
        iter_message_evaluation_messages_for_sessions(
            ExperimentSession.objects.filter(team=team, external_id__in=external_ids),
            filter_params=filter_params,
            timezone=timezone,
        )
    )


def message_dataset(team, name="Message Dataset") -> EvaluationDataset:
    """Create an empty message-mode dataset for `team`."""
    return EvaluationDataset.objects.create(team=team, name=name, evaluation_mode=EvaluationMode.MESSAGE)
