import pytest

from apps.experiments.migration_utils import reconcile_connect_participants
from apps.experiments.models import ExperimentSession, Participant, ParticipantData
from apps.utils.factories.experiment import ExperimentFactory, ExperimentSessionFactory


@pytest.mark.django_db()
def test_reconcile_connect_participants():
    """
    Test cases:

    Case 1 - Two participants with identifiers differing only by case chatted to the same bot. The sessions should be
            merged. The lower cased participant should retain all data.
    Case 2 - A participant with an uppercase identifier that has no lowercase counterpart should be changed to lowercase
    Case 3 - The system metadata of the uppercase participant is moved to the lowercase participant if the
             lowercase participant's system metadata is empty
    Case 4 - The participant data of the uppercase participant is moved to the lowercase participant if the
             lowercase participant's data for a specific chatbot is missing (meaning that participant has never
             chatted to that chatbot)
    """
    experiment1 = ExperimentFactory()
    team = experiment1.team

    # Setup case 1 - Same team and chatbot and same identifier differing only by case
    case1_uc_session = ExperimentSessionFactory(
        experiment=experiment1, participant__platform="commcare_connect", participant__identifier="ABC123"
    )

    case1_lc_session = ExperimentSessionFactory(
        team=team, experiment=experiment1, participant__platform="commcare_connect", participant__identifier="abc123"
    )

    uc_participant = case1_uc_session.participant
    lc_participant = case1_lc_session.participant

    # Setup case 2 - This participant's identifier should be lowercased
    case2_session = ExperimentSessionFactory(
        team=team, experiment=experiment1, participant__platform="commcare_connect", participant__identifier="DEF456"
    )

    # Setup case 3 - Upper cased participant has system metadata, lower case participant does not
    ParticipantData.objects.create(
        team=team,
        experiment=experiment1,
        participant=uc_participant,
        system_metadata={"orignated_from_uppercase": True},
    )
    ParticipantData.objects.create(team=team, experiment=experiment1, participant=lc_participant, system_metadata={})

    # Setup case 4 - upper case participant has participant data, lower case participant does not
    # We create a new session for the upper case participant but with a different experiment
    experiment2 = ExperimentFactory(team=team)
    ExperimentSessionFactory(experiment=experiment2, participant=uc_participant)
    ParticipantData.objects.create(team=team, experiment=experiment2, participant=uc_participant)
    assert lc_participant.data_set.filter(experiment=experiment2).exists() is False

    reconcile_connect_participants(Participant)

    # Assert Case 1 - Sessions should be moved to the lower cased participant
    case1_uc_session.refresh_from_db()
    case1_lc_session.refresh_from_db()
    assert case1_uc_session.participant == case1_lc_session.participant
    assert case1_uc_session.participant.identifier == "abc123"

    # Make sure the upper cased participant has been deleted
    with pytest.raises(Participant.DoesNotExist):
        Participant.objects.get(identifier="ABC123", team=team)

    # Assert Case 2 - Participant identifier should be lowercased
    case2_session.refresh_from_db()
    assert case2_session.participant.identifier == "def456"
    # Make sure the upper cased participant isn't there anymore
    with pytest.raises(Participant.DoesNotExist):
        Participant.objects.get(identifier="DEF456", team=team)

    # Assert Case 3 - System metadata should be moved to the lower cased participant
    data_set = lc_participant.data_set.get(experiment=experiment1)
    assert data_set.system_metadata == {"orignated_from_uppercase": True}

    # Assert Case 4 - Participant data should be moved to the lower cased participant
    assert lc_participant.data_set.filter(experiment=experiment2).exists()

    # From case 1 and 4's setup, we expect 3 sessions for the lower cased participant now
    assert ExperimentSession.objects.filter(participant__identifier="abc123", team=team).count() == 3


# def _setup_test_cases(team):
