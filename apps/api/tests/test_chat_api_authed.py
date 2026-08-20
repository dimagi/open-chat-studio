"""Tests for authenticated access to the Chat API.

When access is authenticated, it implies that the chat widget is being hosted on the same
OCS instance as the bot (to allow the session cookie to work). In this case, we should enforce
that the `remote_id` matches the authenticated user's email address.

Being logged in is not access to every chatbot: the caller must also either belong to the
chatbot's team or present its widget embed key (the site help widget's route — its users are
logged in but are not members of the support bot's team). Every test here runs over both.
"""

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.channels.models import ChannelPlatform
from apps.experiments.models import ExperimentSession, Participant, ParticipantData
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentSessionFactory
from apps.utils.factories.team import TeamWithUsersFactory

WIDGET_TOKEN = "test_widget_token_123456789012"
WIDGET_DOMAIN = "ocs.example.com"


@pytest.fixture()
def widget_channel(experiment):
    return ExperimentChannelFactory.create(
        experiment=experiment,
        team=experiment.team,
        platform=ChannelPlatform.EMBEDDED_WIDGET,
        extra_data={"widget_token": WIDGET_TOKEN, "allowed_domains": [WIDGET_DOMAIN]},
    )


@pytest.fixture(params=["team_member", "embed_key"])
def auth_route(request):
    """The two ways a session-authenticated caller may be authorized for a chatbot."""
    return request.param


@pytest.fixture()
def authed_user(auth_route, experiment):
    if auth_route == "team_member":
        return experiment.team.members.first()
    other_team = TeamWithUsersFactory.create()
    return other_team.members.first()


@pytest.fixture()
def authed_client(auth_route, authed_user, widget_channel):
    client = APIClient()
    client.login(username=authed_user.email, password="password")
    if auth_route == "embed_key":
        client.credentials(HTTP_X_EMBED_KEY=WIDGET_TOKEN, HTTP_ORIGIN=f"https://{WIDGET_DOMAIN}")
    return client


@pytest.fixture()
def non_member_client():
    """A logged-in user with no relationship to the chatbot's team and no embed key."""
    user = TeamWithUsersFactory.create().members.first()
    client = APIClient()
    client.login(username=user.email, password="password")
    return client, user


@pytest.fixture()
def session(experiment):
    return ExperimentSessionFactory.create(experiment=experiment)


@pytest.mark.django_db()
def test_start_chat_session_with_auth(authed_user, authed_client, experiment):
    url = reverse("api:chat:start-session")
    data = {"chatbot_id": experiment.public_id, "participant_remote_id": authed_user.email}
    response = authed_client.post(url, data=data, format="json")
    assert response.status_code == 201
    response_json = response.json()
    assert response_json["participant"]["identifier"] == authed_user.email


@pytest.mark.django_db()
def test_start_chat_session_with_auth_requires_remote_id(authed_client, experiment):
    url = reverse("api:chat:start-session")
    data = {"chatbot_id": experiment.public_id}
    response = authed_client.post(url, data=data, format="json")
    assert response.status_code == 400


@pytest.mark.django_db()
def test_start_chat_session_with_auth_requires_remote_id_to_match_user(authed_client, experiment):
    url = reverse("api:chat:start-session")
    data = {"chatbot_id": experiment.public_id, "participant_remote_id": "not the user's email"}
    response = authed_client.post(url, data=data, format="json")
    assert response.status_code == 400


@pytest.mark.django_db()
def test_start_chat_session_with_session_state(authed_user, authed_client, experiment):
    url = reverse("api:chat:start-session")
    data = {
        "chatbot_id": experiment.public_id,
        "session_data": {"ref": "123"},
        "participant_remote_id": authed_user.email,
    }
    response = authed_client.post(url, data=data, format="json")
    assert response.status_code == 201
    session = ExperimentSession.objects.get(external_id=response.json()["session_id"])
    assert session.state == {"ref": "123"}


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("participant_remote_id", "status_code"), [(None, 400), ("", 400), ("123", 400), ("user_email", 201)]
)
def test_start_chat_session_requires_auth_when_not_public(
    authed_user, authed_client, experiment, participant_remote_id, status_code
):
    url = reverse("api:chat:start-session")
    experiment.participant_allowlist = [authed_user.email]
    experiment.save()
    data = {"chatbot_id": experiment.public_id}
    if participant_remote_id is not None:
        data["participant_remote_id"] = participant_remote_id
    if participant_remote_id == "user_email":
        data["participant_remote_id"] = authed_user.email
    response = authed_client.post(url, data=data, format="json")
    assert response.status_code == status_code


@pytest.mark.django_db()
def test_start_chat_session_with_name(authed_user, authed_client, experiment):
    url = reverse("api:chat:start-session")
    name = "John Doe"
    session_state = {"ref": "abc123"}

    data = {
        "chatbot_id": experiment.public_id,
        "participant_remote_id": authed_user.email,
        "participant_name": name,
        "session_data": session_state,
    }

    response = authed_client.post(url, data=data, format="json")
    assert response.status_code == 201
    response_json = response.json()

    participant = Participant.objects.get(identifier=response_json["participant"]["identifier"])
    assert response_json["participant"]["identifier"] == authed_user.email

    participant_data = ParticipantData.objects.get(participant=participant, experiment=experiment)
    assert participant_data.data.get("name") == name

    session = ExperimentSession.objects.get(external_id=response_json["session_id"])
    assert session.state == session_state


@pytest.mark.django_db()
def test_embed_key_route_keeps_the_authenticated_user_as_the_participant(experiment, widget_channel):
    """The embed key authorizes; it does not downgrade the caller to an anonymous participant."""
    user = TeamWithUsersFactory.create().members.first()
    client = APIClient()
    client.login(username=user.email, password="password")
    client.credentials(HTTP_X_EMBED_KEY=WIDGET_TOKEN, HTTP_ORIGIN=f"https://{WIDGET_DOMAIN}")

    url = reverse("api:chat:start-session")
    data = {"chatbot_id": experiment.public_id, "participant_remote_id": user.email}
    response = client.post(url, data=data, format="json")

    assert response.status_code == 201
    session = ExperimentSession.objects.get(external_id=response.json()["session_id"])
    assert session.participant.user_id == user.id
    assert session.participant.identifier == user.email


@pytest.mark.django_db()
class TestChannelAttribution:
    """A widget's own channel owns the session whenever its embed key rides along, so the same
    widget cannot produce two channels — and two participants — depending on who is looking."""

    def _start(self, user, experiment, **extra):
        client = APIClient()
        client.login(username=user.email, password="password")
        url = reverse("api:chat:start-session")
        data = {"chatbot_id": experiment.public_id, "participant_remote_id": user.email}
        response = client.post(url, data=data, format="json", **extra)
        assert response.status_code == 201, response.json()
        return ExperimentSession.objects.get(external_id=response.json()["session_id"])

    def test_non_member_with_embed_key_lands_on_the_widget_channel(self, experiment, widget_channel):
        user = TeamWithUsersFactory.create().members.first()
        session = self._start(user, experiment, HTTP_X_EMBED_KEY=WIDGET_TOKEN, HTTP_ORIGIN=f"https://{WIDGET_DOMAIN}")
        assert session.experiment_channel == widget_channel
        assert session.participant.platform == ChannelPlatform.EMBEDDED_WIDGET

    def test_team_member_with_embed_key_lands_on_the_same_channel(self, experiment, widget_channel):
        """Membership short-circuits authorization, but it must not change attribution."""
        member = experiment.team.members.first()
        session = self._start(member, experiment, HTTP_X_EMBED_KEY=WIDGET_TOKEN, HTTP_ORIGIN=f"https://{WIDGET_DOMAIN}")
        assert session.experiment_channel == widget_channel
        assert session.participant.platform == ChannelPlatform.EMBEDDED_WIDGET

    def test_team_member_without_embed_key_stays_on_the_api_channel(self, experiment, widget_channel):
        member = experiment.team.members.first()
        session = self._start(member, experiment)
        assert session.experiment_channel.platform == ChannelPlatform.API
        assert session.participant.platform == ChannelPlatform.API

    def test_an_invalid_key_does_not_attribute_to_the_widget_channel(self, experiment, widget_channel):
        """A member gets in on membership alone; a key that fails its checks attributes nothing."""
        member = experiment.team.members.first()
        session = self._start(member, experiment, HTTP_X_EMBED_KEY=WIDGET_TOKEN, HTTP_ORIGIN="https://evil.com")
        assert session.experiment_channel.platform == ChannelPlatform.API

    def test_widget_version_is_recorded_for_the_site_widget(self, experiment, widget_channel):
        """The site widget is now visible to the version telemetry that drives the auth-level ratchet."""
        user = TeamWithUsersFactory.create().members.first()
        self._start(
            user,
            experiment,
            HTTP_X_EMBED_KEY=WIDGET_TOKEN,
            HTTP_ORIGIN=f"https://{WIDGET_DOMAIN}",
            HTTP_X_OCS_WIDGET_VERSION="0.9.0",
        )
        widget_channel.refresh_from_db()
        assert widget_channel.widget_version == "0.9.0"


@pytest.mark.django_db()
class TestNonMemberAccess:
    """A logged-in user may not start a session on another team's chatbot without its embed key."""

    def _start(self, client, experiment, user, **extra):
        url = reverse("api:chat:start-session")
        data = {"chatbot_id": experiment.public_id, "participant_remote_id": user.email}
        return client.post(url, data=data, format="json", **extra)

    def test_no_embed_key(self, non_member_client, experiment, widget_channel):
        client, user = non_member_client
        response = self._start(client, experiment, user)
        assert response.status_code == 403
        assert response.json()["error"] == "You do not have access to this chatbot"

    @pytest.mark.parametrize(
        "extra",
        [
            pytest.param(
                {"HTTP_X_EMBED_KEY": "wrong_token", "HTTP_ORIGIN": f"https://{WIDGET_DOMAIN}"}, id="wrong_key"
            ),
            pytest.param({"HTTP_X_EMBED_KEY": WIDGET_TOKEN, "HTTP_ORIGIN": "https://evil.com"}, id="disallowed_domain"),
            pytest.param({"HTTP_X_EMBED_KEY": WIDGET_TOKEN}, id="no_origin_or_referer"),
        ],
    )
    def test_embed_key_rejected(self, non_member_client, experiment, widget_channel, extra):
        client, user = non_member_client
        response = self._start(client, experiment, user, **extra)
        assert response.status_code == 403

    def test_embed_key_of_another_chatbot(self, non_member_client, experiment, widget_channel):
        """A key is only good for the chatbot it belongs to."""
        other_channel = ExperimentChannelFactory.create(
            platform=ChannelPlatform.EMBEDDED_WIDGET,
            extra_data={"widget_token": "someone_elses_token", "allowed_domains": [WIDGET_DOMAIN]},
        )
        client, user = non_member_client
        response = self._start(
            client,
            experiment,
            user,
            HTTP_X_EMBED_KEY=other_channel.extra_data["widget_token"],
            HTTP_ORIGIN=f"https://{WIDGET_DOMAIN}",
        )
        assert response.status_code == 403

    def test_embed_key_does_not_grant_version_selection(self, non_member_client, experiment, widget_channel):
        """Choosing a chatbot version stays a team-member capability."""
        client, user = non_member_client
        url = reverse("api:chat:start-session")
        data = {
            "chatbot_id": experiment.public_id,
            "participant_remote_id": user.email,
            "version_number": 0,
        }
        response = client.post(
            url, data=data, format="json", HTTP_X_EMBED_KEY=WIDGET_TOKEN, HTTP_ORIGIN=f"https://{WIDGET_DOMAIN}"
        )
        assert response.status_code == 403
