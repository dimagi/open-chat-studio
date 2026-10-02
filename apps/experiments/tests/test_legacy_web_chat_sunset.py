from datetime import timedelta
from unittest import mock

import pytest
from django.contrib.sites.models import Site
from django.urls import NoReverseMatch, reverse
from time_machine import travel
from waffle.testutils import override_flag

from apps.channels.models import ChannelPlatform, ExperimentChannel
from apps.experiments.const import LEGACY_WEB_CHAT_SUCCESSOR_URL, LEGACY_WEB_CHAT_SUNSET_AT
from apps.experiments.models import ExperimentSession, SessionStatus
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ChatMessageFactory, ExperimentFactory, ExperimentSessionFactory
from apps.utils.factories.team import TeamWithUsersFactory

TOKEN = "public_token_1234567890123456789012"
FLAG = "flag_sunset_legacy_web_chat_enforce"
AFTER = LEGACY_WEB_CHAT_SUNSET_AT + timedelta(days=1)
EXPECTED_LINK = f'<{LEGACY_WEB_CHAT_SUCCESSOR_URL}>; rel="successor-version"'


@pytest.fixture(autouse=True)
def _canonical_site(db):
    Site.objects.filter(id=1).update(domain="ocs.example.com")
    Site.objects.clear_cache()
    yield
    Site.objects.clear_cache()


def _experiment(**kwargs):
    return ExperimentFactory.create(team=TeamWithUsersFactory.create(), consent_form=None, **kwargs)


def _public_channel(experiment, *, enabled=True, token=TOKEN):
    return ExperimentChannelFactory.create(
        team=experiment.team,
        experiment=experiment,
        platform=ChannelPlatform.PUBLIC,
        enabled=enabled,
        extra_data={"widget_token": token},
    )


def _web_session(experiment, status=SessionStatus.ACTIVE):
    channel = ExperimentChannel.objects.get_team_web_channel(experiment.team)
    return ExperimentSessionFactory.create(experiment=experiment, experiment_channel=channel, status=status)


def _participant_session(experiment, status, client):
    """A web session whose participant is a logged-in team member, which the session views admit."""
    user = experiment.team.members.first()
    session = _web_session(experiment, status=status)
    session.participant.user = user
    session.participant.save()
    client.force_login(user)
    return session


def _start_url(experiment, mount="experiments"):
    return reverse(f"{mount}:start_session_public", args=[experiment.team.slug, experiment.public_id])


def _session_url(name, session):
    experiment = session.experiment
    return reverse(name, args=[experiment.team.slug, experiment.public_id, session.external_id])


def _assert_sunset_headers(response):
    assert response.headers["Deprecation"] == "true"
    assert "Sunset" in response.headers
    assert response.headers["Link"] == EXPECTED_LINK


@pytest.mark.django_db()
@pytest.mark.parametrize("mount", ["experiments", "chatbots"])
@mock.patch("apps.experiments.services.enqueue_static_triggers")
def test_start_carries_sunset_headers(_triggers, mount, client):
    experiment = _experiment()
    response = client.get(_start_url(experiment, mount))
    assert response.status_code == 302
    _assert_sunset_headers(response)
    assert ExperimentSession.objects.filter(experiment=experiment).count() == 1


@pytest.mark.django_db()
@pytest.mark.parametrize("mount", ["experiments", "chatbots"])
def test_start_redirects_to_a_live_public_link(mount, client):
    experiment = _experiment()
    experiment.create_new_version(make_default=True)
    channel = _public_channel(experiment)

    response = client.get(_start_url(experiment, mount))

    assert response.status_code == 302
    assert response.url == channel.public_url
    _assert_sunset_headers(response)
    assert not ExperimentSession.objects.filter(experiment=experiment).exists()


@pytest.mark.django_db()
def test_post_redirects_without_creating_a_session(client):
    experiment = ExperimentFactory.create(team=TeamWithUsersFactory.create())
    experiment.create_new_version(make_default=True)
    channel = _public_channel(experiment)

    response = client.post(
        _start_url(experiment),
        data={"identifier": "a@example.com", "consent_agreement": True, "experiment_id": str(experiment.id)},
    )

    assert response.status_code == 302
    assert response.url == channel.public_url
    assert not ExperimentSession.objects.filter(experiment=experiment).exists()


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("publish", "channel_kwargs"),
    [
        pytest.param(False, {}, id="nothing-published"),
        pytest.param(True, {"enabled": False}, id="channel-disabled"),
        pytest.param(True, {"token": ""}, id="no-token"),
    ],
)
@mock.patch("apps.experiments.services.enqueue_static_triggers")
def test_start_keeps_serving_when_the_public_link_is_not_live(_triggers, publish, channel_kwargs, client):
    experiment = _experiment()
    if publish:
        experiment.create_new_version(make_default=True)
    _public_channel(experiment, **channel_kwargs)

    response = client.get(_start_url(experiment))

    assert response.status_code == 302
    assert "/c/" not in response.url
    assert ExperimentSession.objects.filter(experiment=experiment).count() == 1


@pytest.mark.django_db()
def test_invite_start_does_not_redirect_to_the_public_link(client):
    experiment = _experiment()
    experiment.create_new_version(make_default=True)
    _public_channel(experiment)
    session = _web_session(experiment, status=SessionStatus.SETUP)

    response = client.get(_session_url("experiments:start_session_from_invite", session))

    assert "/c/" not in response.get("Location", "")
    _assert_sunset_headers(response)


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("url_name", "status"),
    [
        pytest.param("chatbots:chatbot_chat", SessionStatus.ACTIVE, id="public-chat-page"),
        pytest.param("experiments:experiment_review", SessionStatus.PENDING_REVIEW, id="review"),
        pytest.param("experiments:experiment_complete", SessionStatus.COMPLETE, id="complete"),
    ],
)
def test_session_pages_carry_sunset_headers(url_name, status, client):
    session = _participant_session(_experiment(), status, client)

    response = client.get(_session_url(url_name, session))

    assert response.status_code == 200
    _assert_sunset_headers(response)


@pytest.mark.django_db()
def test_state_redirects_carry_sunset_headers(client):
    session = _web_session(_experiment(), status=SessionStatus.COMPLETE)
    response = client.get(_session_url("chatbots:chatbot_chat", session))
    assert response.status_code == 302
    _assert_sunset_headers(response)


@pytest.mark.django_db()
def test_verify_token_carries_sunset_headers(client):
    experiment = _experiment()
    url = reverse("experiments:verify_public_chat_token", args=[experiment.team.slug, experiment.public_id, "bad"])
    response = client.get(url)
    assert response.status_code == 302
    _assert_sunset_headers(response)


@pytest.mark.django_db()
def test_rating_carries_sunset_headers(client):
    session = _web_session(_experiment())
    message = ChatMessageFactory.create(chat=session.chat, message_type="ai", content="hello")
    url = reverse("experiments:rate_message", args=[session.team.slug, message.id, "👍"])

    response = client.post(url)

    assert response.status_code == 200
    _assert_sunset_headers(response)


@pytest.mark.django_db()
def test_full_page_chat_carries_sunset_headers(client):
    experiment = _experiment()
    session = _participant_session(experiment, SessionStatus.ACTIVE, client)

    url = reverse(
        "chatbots:chatbot_chat_session",
        args=[experiment.team.slug, experiment.id, experiment.version_number, session.id],
    )
    response = client.get(url)

    assert response.status_code == 200
    _assert_sunset_headers(response)


@pytest.mark.django_db()
def test_the_date_alone_does_not_close_the_entry_points(client):
    experiment = _experiment()
    experiment.create_new_version(make_default=True)
    _public_channel(experiment)
    with travel(AFTER, tick=False), override_flag(FLAG, active=False):
        response = client.get(_start_url(experiment))
    assert response.status_code == 302


@pytest.mark.django_db()
@pytest.mark.parametrize("mount", ["experiments", "chatbots"])
def test_open_gate_returns_gone_naming_the_public_link(mount, client):
    experiment = _experiment()
    experiment.create_new_version(make_default=True)
    channel = _public_channel(experiment)
    with travel(AFTER, tick=False), override_flag(FLAG, active=True):
        response = client.get(_start_url(experiment, mount))
    assert response.status_code == 410
    assert channel.public_url in response.content.decode()


@pytest.mark.django_db()
def test_open_gate_names_the_docs_without_a_public_link(client):
    experiment = _experiment()
    with travel(AFTER, tick=False), override_flag(FLAG, active=True):
        response = client.get(_start_url(experiment))
    assert response.status_code == 410
    assert LEGACY_WEB_CHAT_SUCCESSOR_URL in response.content.decode()
    assert not ExperimentSession.objects.filter(experiment=experiment).exists()


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("url_name", "status"),
    [
        pytest.param("experiments:start_session_from_invite", SessionStatus.SETUP, id="invite-start"),
        pytest.param("chatbots:chatbot_chat", SessionStatus.ACTIVE, id="public-chat-page"),
    ],
)
def test_open_gate_closes_session_entry_points(url_name, status, client):
    session = _web_session(_experiment(), status=status)
    with travel(AFTER, tick=False), override_flag(FLAG, active=True):
        response = client.get(_session_url(url_name, session))
    assert response.status_code == 410


@pytest.mark.django_db()
def test_open_gate_closes_the_verify_token_link(client):
    experiment = _experiment()
    url = reverse("experiments:verify_public_chat_token", args=[experiment.team.slug, experiment.public_id, "bad"])
    with travel(AFTER, tick=False), override_flag(FLAG, active=True):
        response = client.get(url)
    assert response.status_code == 410


@pytest.mark.django_db()
def test_open_gate_leaves_the_review_page_served(client):
    session = _participant_session(_experiment(), SessionStatus.PENDING_REVIEW, client)
    with travel(AFTER, tick=False), override_flag(FLAG, active=True):
        client.force_login(session.participant.user)
        response = client.get(_session_url("experiments:experiment_review", session))
    assert response.status_code == 200


def test_the_authed_web_session_route_is_gone():
    with pytest.raises(NoReverseMatch):
        reverse("chatbots:start_authed_web_session", args=["team", 1, 0])
