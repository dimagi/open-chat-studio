import pytest

from apps.chat.models import ChatMessage, ChatMessageType
from apps.experiments.models import ParticipantData
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory, ParticipantFactory


@pytest.mark.django_db()
def test_get_experiments_for_display():
    participant = ParticipantFactory.create()
    session = ExperimentSessionFactory.create(participant=participant)
    message = ChatMessage.objects.create(chat=session.chat, message_type=ChatMessageType.HUMAN, content="Hi")

    experiment = participant.get_experiments_for_display()[0]
    assert experiment.joined_on == session.created_at
    assert experiment.last_message == message.created_at


@pytest.mark.django_db()
def test_get_experiments_for_display_no_messages():
    participant = ParticipantFactory.create()
    session = ExperimentSessionFactory.create(participant=participant)

    experiment = participant.get_experiments_for_display()[0]
    assert experiment.joined_on == session.created_at
    assert experiment.last_message is None


@pytest.mark.django_db()
def test_get_experiments_for_display_no_session():
    participant = ParticipantFactory.create()
    experiment = ExperimentFactory.create(team=participant.team)
    ParticipantData.objects.create(
        participant=participant, experiment=experiment, team=participant.team, data={"foo": "bar"}
    )
    experiment = participant.get_experiments_for_display()[0]
    assert experiment.joined_on is None
    assert experiment.last_message is None
