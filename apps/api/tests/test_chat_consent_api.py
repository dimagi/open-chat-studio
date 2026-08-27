from unittest import mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework.test import APIClient

from apps.api.chat_consent import consent_block, participant_data_for
from apps.experiments.models import ParticipantData, SessionStatus
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
    data.record_consent(session.experiment.consent_form_id)

    assert consent_block(session.experiment, data) == {
        "required": False,
        "form_version_id": session.experiment.consent_form_id,
        "text": None,
    }


@pytest.mark.django_db()
def test_consent_block_is_required_again_once_the_form_is_republished(session):
    data = ParticipantData.objects.create(
        team=session.team, participant=session.participant, experiment=session.experiment
    )
    data.record_consent(session.experiment.consent_form_id)
    session.experiment.consent_form.consent_text = "Please agree to the **new** terms"
    session.experiment.consent_form.save()
    republished = session.experiment.create_new_version(make_default=True)

    assert consent_block(republished, data) == {
        "required": True,
        "form_version_id": republished.consent_form_id,
        "text": "<p>Please agree to the <strong>new</strong> terms</p>",
    }


@pytest.mark.django_db()
def test_consent_block_does_not_treat_consent_from_another_channel_as_accepting_the_form(session):
    data = ParticipantData.objects.create(
        team=session.team, participant=session.participant, experiment=session.experiment
    )
    data.update_consent(True)

    assert consent_block(session.experiment, data)["required"] is True


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


def _poll(api_client, session, **extra):
    url = reverse("api:chat:poll-response", kwargs={"session_id": session.external_id})
    return api_client.get(url, **extra)


@pytest.mark.django_db()
def test_poll_skips_the_participant_data_query_when_the_version_has_no_form(api_client, plain_experiment):
    plain_session = ExperimentSessionFactory.create(experiment=plain_experiment, session_token_required=False)

    with mock.patch("apps.api.chat_consent.participant_data_for") as mocked:
        response = _poll(api_client, plain_session)

    assert response.status_code == 200
    mocked.assert_not_called()


@pytest.mark.django_db()
def test_poll_queries_participant_data_when_the_version_has_a_form(api_client, session):
    with mock.patch("apps.api.chat_consent.participant_data_for", wraps=participant_data_for) as mocked:
        response = _poll(api_client, session)

    assert response.status_code == 200
    mocked.assert_called_once_with(session)


@pytest.mark.django_db()
def test_poll_reports_consent_required_before_acceptance(api_client, session):
    response = _poll(api_client, session, HTTP_X_OCS_WIDGET_VERSION="0.13.0")

    assert response.status_code == 200
    assert response.json()["consent"]["required"] is True
    assert response.json()["consent"]["form_version_id"] == session.experiment.consent_form_id


@pytest.mark.django_db()
def test_poll_reports_consent_satisfied_after_acceptance(api_client, session):
    ParticipantData.objects.create(
        team=session.team, participant=session.participant, experiment=session.experiment
    ).record_consent(session.experiment.consent_form_id)

    response = _poll(api_client, session, HTTP_X_OCS_WIDGET_VERSION="0.13.0")

    assert response.json()["consent"] == {
        "required": False,
        "form_version_id": session.experiment.consent_form_id,
        "text": None,
    }


def _consent(api_client, session, form_version_id, **extra):
    url = reverse("api:chat:record-consent", kwargs={"session_id": session.external_id})
    return api_client.post(url, data={"form_version_id": form_version_id}, format="json", **extra)


@pytest.mark.django_db()
def test_recording_consent_marks_the_participant_and_returns_no_content(api_client, session):
    response = _consent(api_client, session, session.experiment.consent_form_id)

    assert response.status_code == 204
    assert participant_data_for(session).has_consented_to(session.experiment.consent_form_id)


@pytest.mark.django_db()
def test_consent_carries_over_to_the_participants_later_sessions(api_client, session):
    _consent(api_client, session, session.experiment.consent_form_id)
    later_session = ExperimentSessionFactory.create(
        experiment=session.experiment, participant=session.participant, session_token_required=False
    )

    response = _poll(api_client, later_session, HTTP_X_OCS_WIDGET_VERSION="0.13.0")

    assert response.json()["consent"]["required"] is False


@pytest.mark.django_db()
def test_a_republished_form_prompts_the_participant_again(api_client, session):
    _consent(api_client, session, session.experiment.consent_form_id)
    session.experiment.consent_form.consent_text = "New terms"
    session.experiment.consent_form.save()
    republished = session.experiment.create_new_version(make_default=True)

    response = _poll(api_client, session, HTTP_X_OCS_WIDGET_VERSION="0.13.0")

    assert response.json()["consent"] == {
        "required": True,
        "form_version_id": republished.consent_form_id,
        "text": "<p>New terms</p>",
    }


@pytest.mark.django_db()
def test_recording_consent_twice_is_a_no_op(api_client, session):
    _consent(api_client, session, session.experiment.consent_form_id)
    response = _consent(api_client, session, session.experiment.consent_form_id)

    assert response.status_code == 204


@pytest.mark.django_db()
def test_recording_consent_against_a_stale_form_is_refused_with_the_current_form(api_client, session):
    response = _consent(api_client, session, session.experiment.consent_form_id + 1)

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "consent_stale"
    assert body["consent"]["required"] is True
    assert body["consent"]["form_version_id"] == session.experiment.consent_form_id
    assert participant_data_for(session) is None


@pytest.mark.django_db()
def test_recording_consent_on_a_chatbot_without_a_form_is_stale(api_client, plain_experiment):
    plain_session = ExperimentSessionFactory.create(experiment=plain_experiment, session_token_required=False)

    response = _consent(api_client, plain_session, 1)

    assert response.status_code == 409


@pytest.mark.django_db()
def test_recording_consent_on_an_ended_session_is_refused(api_client, session):
    session.update_status(SessionStatus.COMPLETE)

    response = _consent(api_client, session, session.experiment.consent_form_id)

    assert response.status_code == 400


@pytest.mark.django_db()
def test_recording_consent_without_a_session_token_is_refused(api_client, consent_experiment):
    token_required_session = ExperimentSessionFactory.create(experiment=consent_experiment)

    response = _consent(api_client, token_required_session, consent_experiment.consent_form_id)

    assert response.status_code == 403
    assert response.json()["code"] == "session_token_required"
    assert participant_data_for(token_required_session) is None


@pytest.mark.django_db()
def test_recording_consent_does_not_touch_the_legacy_session_consent_date(api_client, session):
    _consent(api_client, session, session.experiment.consent_form_id)

    session.refresh_from_db()
    assert session.consent_date is None


def _send(api_client, session, **extra):
    url = reverse("api:chat:send-message", kwargs={"session_id": session.external_id})
    return api_client.post(url, data={"message": "hi"}, format="json", **extra)


def _upload(api_client, session, **extra):
    url = reverse("api:chat:upload-file", kwargs={"session_id": session.external_id})
    upload = SimpleUploadedFile("note.txt", b"hello", content_type="text/plain")
    return api_client.post(url, data={"files": [upload]}, format="multipart", **extra)


@pytest.mark.django_db()
@pytest.mark.parametrize("call", [pytest.param(_send, id="send"), pytest.param(_upload, id="upload")])
def test_release_b_widget_is_refused_until_consent_is_recorded(api_client, session, call):
    response = call(api_client, session, HTTP_X_OCS_WIDGET_VERSION="0.13.0")

    assert response.status_code == 403
    body = response.json()
    assert body["code"] == "consent_required"
    assert body["consent"]["form_version_id"] == session.experiment.consent_form_id
    assert body["consent"]["text"]


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("call", "expected_status"),
    [pytest.param(_send, 202, id="send"), pytest.param(_upload, 201, id="upload")],
)
def test_release_b_widget_passes_once_consent_is_recorded(api_client, session, call, expected_status):
    _consent(api_client, session, session.experiment.consent_form_id, HTTP_X_OCS_WIDGET_VERSION="0.13.0")

    if call is _send:
        with mock.patch("apps.api.views.chat.get_response_for_webchat_task"):
            response = call(api_client, session, HTTP_X_OCS_WIDGET_VERSION="0.13.0")
    else:
        response = call(api_client, session, HTTP_X_OCS_WIDGET_VERSION="0.13.0")

    assert response.status_code == expected_status


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "widget_version",
    [
        pytest.param("0.11.0", id="release-a"),
        pytest.param("0.12.0", id="published-without-consent-panel"),
        pytest.param(None, id="no-header"),
    ],
)
def test_older_widgets_and_api_callers_are_not_gated(api_client, session, widget_version):
    extra = {"HTTP_X_OCS_WIDGET_VERSION": widget_version} if widget_version else {}

    with mock.patch("apps.api.views.chat.get_response_for_webchat_task"):
        response = _send(api_client, session, **extra)

    assert response.status_code == 202


@pytest.mark.django_db()
def test_poll_is_never_gated(api_client, session):
    assert _send(api_client, session, HTTP_X_OCS_WIDGET_VERSION="0.13.0").status_code == 403

    response = _poll(api_client, session, HTTP_X_OCS_WIDGET_VERSION="0.13.0")

    assert response.status_code == 200
