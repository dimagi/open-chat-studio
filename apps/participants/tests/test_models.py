import pytest

from apps.chat.models import ChatMessage, ChatMessageType
from apps.participants.models import ParticipantData
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory, ParticipantFactory


@pytest.mark.django_db()
def test_get_experiments_for_display():
    participant = ParticipantFactory()
    session = ExperimentSessionFactory(participant=participant)
    message = ChatMessage.objects.create(chat=session.chat, message_type=ChatMessageType.HUMAN, content="Hi")

    experiment = participant.get_experiments_for_display()[0]
    assert experiment.joined_on == session.created_at
    assert experiment.last_message == message.created_at


@pytest.mark.django_db()
def test_get_experiments_for_display_no_messages():
    participant = ParticipantFactory()
    session = ExperimentSessionFactory(participant=participant)

    experiment = participant.get_experiments_for_display()[0]
    assert experiment.joined_on == session.created_at
    assert experiment.last_message is None


@pytest.mark.django_db()
def test_get_experiments_for_display_no_session():
    participant = ParticipantFactory()
    experiment = ExperimentFactory(team=participant.team)
    ParticipantData.objects.create(
        participant=participant, experiment=experiment, team=participant.team, data={"foo": "bar"}
    )
    experiment = participant.get_experiments_for_display()[0]
    assert experiment.joined_on is None
    assert experiment.last_message is None
