import pytest
from django.urls import reverse
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


@pytest.fixture()
def plain_experiment(team_with_users):
    return ExperimentFactory.create(team=team_with_users, consent_form=None)


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


def _start(api_client, experiment, **extra):
    url = reverse("api:chat:start-session")
    return api_client.post(url, data={"chatbot_id": experiment.public_id}, format="json", **extra)


@pytest.mark.django_db()
def test_start_reports_consent_required_for_a_consent_form_chatbot(api_client, consent_experiment):
    response = _start(api_client, consent_experiment)

    assert response.status_code == 201
    assert response.json()["consent"] == {
        "required": True,
        "form_version_id": consent_experiment.consent_form_id,
        "text": "<p>Please <strong>agree</strong></p>",
    }


@pytest.mark.django_db()
def test_start_reports_the_published_versions_frozen_form(api_client, consent_experiment):
    published = consent_experiment.create_new_version(make_default=True)

    response = _start(api_client, consent_experiment)

    frozen_form_id = response.json()["consent"]["form_version_id"]
    assert frozen_form_id == published.consent_form_id
    assert frozen_form_id != consent_experiment.consent_form_id


@pytest.mark.django_db()
def test_start_reports_no_consent_needed_without_a_form(api_client, plain_experiment):
    response = _start(api_client, plain_experiment)

    assert response.json()["consent"] == {"required": False, "form_version_id": None, "text": None}
