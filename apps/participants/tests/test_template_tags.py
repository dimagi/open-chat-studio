import pytest

from apps.participants.templatetags.participants import participant_sessions
from apps.utils.factories.experiment import ExperimentSessionFactory, ParticipantFactory


@pytest.mark.django_db()
def test_participant_sessions():
    """The template tag should return all participant sessions for the given experiment"""
    participant = ParticipantFactory()
    participant2 = ParticipantFactory()
    session = ExperimentSessionFactory(participant=participant)
    ExperimentSessionFactory(participant=participant2, experiment=session.experiment)

    sessions = participant_sessions(session.experiment, participant=participant)
    for session in sessions:
        assert session.participant == participant
