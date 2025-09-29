from apps.experiments.migration_utils import reconcile_connect_participants
from apps.experiments.models import Participant
from apps.utils.factories.experiment import ExperimentSessionFactory


def test_reconcile_connect_participants(experiment):
    team = experiment.team

    # session1 should be ported to the participant of session2
    session1 = ExperimentSessionFactory(
        experiment=experiment, participant__platform="commcare_connect", participant__identifier="ABC123"
    )
    session2 = ExperimentSessionFactory(
        team=team, experiment=experiment, participant__platform="commcare_connect", participant__identifier="abc123"
    )

    # session1's participant, different experiment. Should also be ported to session2's participant
    session3 = ExperimentSessionFactory(team=team, participant=session1.participant)

    # Another team, same setup. session4 should go to session5's participant
    session4 = ExperimentSessionFactory(participant__platform="commcare_connect", participant__identifier="ABC123")
    session5 = ExperimentSessionFactory(
        team=session4.team, participant__platform="commcare_connect", participant__identifier="abc123"
    )

    # Totally different participant. This session's participant's identifier should be lowercased
    session6 = ExperimentSessionFactory(
        team=team, experiment=experiment, participant__platform="commcare_connect", participant__identifier="DEF456"
    )

    reconcile_connect_participants(Participant)

    session1.refresh_from_db()
    session2.refresh_from_db()
    session3.refresh_from_db()
    session4.refresh_from_db()
    session5.refresh_from_db()
    session6.refresh_from_db()

    # Check that sessions 1, 2, and 3 are all linked to the same participant (the one with the lowercased identifier)
    assert session1.participant_id == session2.participant_id == session3.participant_id
    # Ensure the uppercase participant has been deleted
    assert Participant.objects.filter(team=team, identifier="ABC123").exists() is False

    assert session4.participant == session5.participant
    assert session6.participant.identifier == "def456"

    # Ensure no participants with uppercase identifiers remain
    assert Participant.objects.filter(team=team, identifier="DEF456").exists() is False
