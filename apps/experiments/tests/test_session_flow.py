import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from apps.experiments.decorators import CHAT_SESSION_ACCESS_COOKIE
from apps.experiments.models import Participant
from apps.utils.factories.experiment import ExperimentSessionFactory


@pytest.fixture()
def session():
    session = ExperimentSessionFactory()
    participant = Participant.objects.create(team=session.team, identifier="test@test.com")
    session.participant = participant
    session.save()
    return session


@pytest.mark.django_db()
def test_session_flow_with_access_cookie(client, session):
    response = client.post(
        reverse(
            "experiments:start_experiment_session",
            args=[session.experiment.team.slug, session.experiment.public_id, session.public_id],
        ),
        data={
            "consent_agreement": "on",
            "experiment_id": session.experiment.id,
            "participant_id": session.participant.id,
        },
    )
    assert response.status_code == 302
    assert CHAT_SESSION_ACCESS_COOKIE in client.cookies

    if session.experiment.pre_survey:
        next_url = reverse(
            "experiments:experiment_pre_survey",
            args=[session.experiment.team.slug, session.experiment.public_id, session.public_id],
        )
    else:
        next_url = reverse(
            "experiments:experiment_chat",
            args=[session.experiment.team.slug, session.experiment.public_id, session.public_id],
        )
    assert response.headers["Location"] == next_url

    response = client.get(next_url)
    assert response.status_code == 200

    # making a request without the chat_session_access cookie should 404
    del client.cookies[CHAT_SESSION_ACCESS_COOKIE]
    response = client.get(next_url)
    assert response.status_code == 404

    # unless the request is for an authenticated user with the view_chat permission
    client.login(username=session.experiment.owner.username, password="password")
    session.experiment.owner.user_permissions.add(Permission.objects.get(codename="view_chat"))
    response = client.get(next_url)
    assert response.status_code == 200


@pytest.mark.django_db()
def test_access_cookie_not_set_on_session_start_get(client, session):
    response = client.get(
        reverse(
            "experiments:start_experiment_session",
            args=[session.experiment.team.slug, session.experiment.public_id, session.public_id],
        ),
    )
    assert response.status_code == 200
    assert CHAT_SESSION_ACCESS_COOKIE not in client.cookies


@pytest.mark.django_db()
def test_access_cookie_not_set_on_session_start_with_inavlid_form(client, session):
    response = client.post(
        reverse(
            "experiments:start_experiment_session",
            args=[session.experiment.team.slug, session.experiment.public_id, session.public_id],
        ),
        data={},
    )
    assert response.status_code == 200
    assert CHAT_SESSION_ACCESS_COOKIE not in client.cookies
