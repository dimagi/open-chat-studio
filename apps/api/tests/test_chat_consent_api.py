import pytest
from rest_framework.test import APIClient

from apps.api.chat_consent import consent_block, participant_data_for
from apps.experiments.models import ParticipantData
from apps.utils.factories.experiment import ConsentFormFactory, ExperimentFactory, ExperimentSessionFactory


@pytest.fixture()
def api_client():
    return APIClient()


@pytest.fixture()
def consent_experiment(team_with_users):
    form = ConsentFormFactory.create(team=team_with_users, consent_text="Please **agree**")
    return ExperimentFactory.create(team=team_with_users, consent_form=form)


@pytest.fixture()
def session(consent_experiment):
    return ExperimentSessionFactory.create(experiment=consent_experiment, session_token_required=False)


@pytest.mark.django_db()
def test_consent_block_without_a_form_is_not_required(experiment):
    experiment.consent_form = None
    assert consent_block(experiment, None) == {"required": False, "form_version_id": None, "text": None}


@pytest.mark.django_db()
def test_consent_block_with_a_form_and_no_participant_data_is_required(consent_experiment):
    block = consent_block(consent_experiment, None)
    assert block == {
        "required": True,
        "form_version_id": consent_experiment.consent_form_id,
        "text": "<p>Please <strong>agree</strong></p>",
    }


@pytest.mark.django_db()
def test_consent_block_after_consent_keeps_the_form_id_and_drops_the_text(session):
    data = ParticipantData.objects.create(
        team=session.team, participant=session.participant, experiment=session.experiment
    )
    data.record_consent()

    assert consent_block(session.experiment, data) == {
        "required": False,
        "form_version_id": session.experiment.consent_form_id,
        "text": None,
    }


@pytest.mark.django_db()
def test_participant_data_for_returns_the_row_for_the_working_chatbot(session):
    assert participant_data_for(session) is None
    row = ParticipantData.objects.create(
        team=session.team, participant=session.participant, experiment=session.experiment
    )
    assert participant_data_for(session) == row
