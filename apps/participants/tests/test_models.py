import pytest

from apps.chat.models import ChatMessage, ChatMessageType
from apps.utils.factories.experiment import ExperimentSessionFactory, ParticipantFactory


@pytest.mark.django_db()
def test_get_experiments_for_display():
    participant = ParticipantFactory()
    session = ExperimentSessionFactory(participant=participant)
    message = ChatMessage.objects.create(chat=session.chat, message_type=ChatMessageType.HUMAN, content="Hi")
    # Add a session, but with no messages yet
    ExperimentSessionFactory(participant=participant, experiment=session.experiment)

    experiment = participant.get_experiments_for_display()[0]
    assert experiment.joined_on == message.created_at
    assert experiment.last_message == message.created_at
