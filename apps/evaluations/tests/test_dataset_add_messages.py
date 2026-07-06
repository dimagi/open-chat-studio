import pytest

from apps.chat.models import ChatMessageType
from apps.evaluations.models import EvaluationDataset, EvaluationMessage, EvaluationMode
from apps.utils.factories.experiment import ChatMessageFactory, ExperimentSessionFactory
from apps.utils.factories.team import TeamFactory


def _chat_message_pair(session):
    """Create a (human, ai) ChatMessage pair on the session's chat."""
    human = ChatMessageFactory.create(message_type=ChatMessageType.HUMAN, content="human", chat=session.chat)
    ai = ChatMessageFactory.create(message_type=ChatMessageType.AI, content="ai", chat=session.chat)
    return human, ai


@pytest.mark.django_db()
def test_message_mode_skips_pair_already_in_dataset():
    """A message whose (input, output) chat-message pair is already in the dataset is skipped."""
    session = ExperimentSessionFactory.create()
    dataset = EvaluationDataset.objects.create(team=session.team, name="ds", evaluation_mode=EvaluationMode.MESSAGE)
    human, ai = _chat_message_pair(session)
    existing = EvaluationMessage.objects.create(
        input={"content": "human"},
        output={"content": "ai"},
        input_chat_message=human,
        expected_output_chat_message=ai,
        session=session,
    )
    dataset.messages.add(existing)

    incoming = EvaluationMessage(
        input={"content": "human"},
        output={"content": "ai"},
        input_chat_message=human,
        expected_output_chat_message=ai,
        session=session,
    )
    created, skipped = dataset.add_messages([incoming])

    assert len(created) == 0
    assert skipped == 1
    assert dataset.messages.count() == 1


@pytest.mark.django_db()
def test_message_mode_skips_duplicate_pair_within_batch():
    """Two incoming messages with the same chat-message pair only add one."""
    session = ExperimentSessionFactory.create()
    dataset = EvaluationDataset.objects.create(team=session.team, name="ds", evaluation_mode=EvaluationMode.MESSAGE)
    human, ai = _chat_message_pair(session)

    incoming = [
        EvaluationMessage(
            input={"content": "human"},
            output={"content": "ai"},
            input_chat_message=human,
            expected_output_chat_message=ai,
            session=session,
        )
        for _ in range(2)
    ]
    created, skipped = dataset.add_messages(incoming)

    assert len(created) == 1
    assert skipped == 1
    assert dataset.messages.count() == 1


@pytest.mark.django_db()
def test_message_mode_allows_same_input_different_output():
    """Pair-based dedup: same input chat message with a different output is a distinct eval."""
    session = ExperimentSessionFactory.create()
    dataset = EvaluationDataset.objects.create(team=session.team, name="ds", evaluation_mode=EvaluationMode.MESSAGE)
    human, ai_1 = _chat_message_pair(session)
    ai_2 = ChatMessageFactory.create(message_type=ChatMessageType.AI, content="ai2", chat=session.chat)

    incoming = [
        EvaluationMessage(
            input={"content": "human"},
            output={"content": "ai"},
            input_chat_message=human,
            expected_output_chat_message=ai_1,
            session=session,
        ),
        EvaluationMessage(
            input={"content": "human"},
            output={"content": "ai2"},
            input_chat_message=human,
            expected_output_chat_message=ai_2,
            session=session,
        ),
    ]
    created, skipped = dataset.add_messages(incoming)

    assert len(created) == 2
    assert skipped == 0
    assert dataset.messages.count() == 2


@pytest.mark.django_db()
def test_session_mode_skips_session_already_in_dataset():
    """A session-mode message whose session is already in the dataset is skipped."""
    session = ExperimentSessionFactory.create()
    dataset = EvaluationDataset.objects.create(team=session.team, name="ds", evaluation_mode=EvaluationMode.SESSION)
    existing = EvaluationMessage.objects.create(input={}, output={}, session=session)
    dataset.messages.add(existing)

    incoming = EvaluationMessage(input={}, output={}, session=session)
    created, skipped = dataset.add_messages([incoming])

    assert len(created) == 0
    assert skipped == 1
    assert dataset.messages.count() == 1


@pytest.mark.django_db()
def test_session_mode_skips_duplicate_session_within_batch():
    session = ExperimentSessionFactory.create()
    dataset = EvaluationDataset.objects.create(team=session.team, name="ds", evaluation_mode=EvaluationMode.SESSION)

    incoming = [
        EvaluationMessage(input={}, output={}, session=session),
        EvaluationMessage(input={}, output={}, session=session),
    ]
    created, skipped = dataset.add_messages(incoming)

    assert len(created) == 1
    assert skipped == 1
    assert dataset.messages.count() == 1


@pytest.mark.django_db()
def test_null_fk_messages_are_never_deduped():
    """Manual/CSV messages have no FKs and must never be treated as duplicates."""
    team = TeamFactory.create()
    dataset = EvaluationDataset.objects.create(team=team, name="ds", evaluation_mode=EvaluationMode.MESSAGE)

    incoming = [
        EvaluationMessage(input={"content": "x"}, output={"content": "y"}),
        EvaluationMessage(input={"content": "x"}, output={"content": "y"}),
    ]
    created, skipped = dataset.add_messages(incoming)

    assert len(created) == 2
    assert skipped == 0
    assert dataset.messages.count() == 2


@pytest.mark.django_db()
def test_messages_are_persisted_and_linked():
    """Incoming unsaved messages are persisted and linked to the dataset."""
    team = TeamFactory.create()
    dataset = EvaluationDataset.objects.create(team=team, name="ds", evaluation_mode=EvaluationMode.MESSAGE)

    incoming = [EvaluationMessage(input={"content": "x"}, output={"content": "y"})]
    created, skipped = dataset.add_messages(incoming)

    assert len(created) == 1
    message = dataset.messages.get()
    assert message.pk is not None
    assert message.input == {"content": "x"}
