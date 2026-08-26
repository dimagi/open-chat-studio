"""The public channel is pinned to the OCS canonical host (spec D3).

Both origin call sites go through `channel_origin_allowed`: the permission class when the embed
key authenticated the request, and `embed_key_authorizes_channel` when a Django session cookie
authenticated first and the key merely rode along.
"""

import pytest
from django.contrib.sites.models import Site
from django.test import RequestFactory
from django.urls import reverse
from rest_framework.test import APIClient

from apps.api.authentication import channel_origin_allowed, embed_key_authorizes_channel
from apps.channels.models import ChannelPlatform
from apps.experiments.models import ExperimentSession
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.user import UserFactory

TOKEN = "public_token_1234567890123456789012"
CANONICAL = "ocs.example.com"


@pytest.fixture(autouse=True)
def _canonical_site(db):
    Site.objects.filter(id=1).update(domain=f"{CANONICAL}:8443", name="OCS")
    Site.objects.clear_cache()
    yield
    Site.objects.clear_cache()


@pytest.fixture()
def public_channel(team_with_users):
    experiment = ExperimentFactory.create(team=team_with_users, consent_form=None)
    experiment.create_new_version(make_default=True)
    return ExperimentChannelFactory.create(
        team=team_with_users, experiment=experiment, platform=ChannelPlatform.PUBLIC, extra_data={"widget_token": TOKEN}
    )


def _request(origin=None, referer=None):
    headers = {}
    if origin:
        headers["HTTP_ORIGIN"] = origin
    if referer:
        headers["HTTP_REFERER"] = referer
    return RequestFactory().post("/api/chat/start/", **headers)


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("origin", "referer", "allowed"),
    [
        pytest.param(f"https://{CANONICAL}", None, True, id="canonical-origin"),
        pytest.param(f"https://{CANONICAL}:8443", None, True, id="port-ignored"),
        pytest.param(f"https://{CANONICAL.upper()}", None, True, id="case-insensitive"),
        pytest.param(None, f"https://{CANONICAL}/c/{TOKEN}/", True, id="referer-fallback"),
        pytest.param("https://evil.example.org", None, False, id="foreign-origin"),
        pytest.param(f"https://sub.{CANONICAL}", None, False, id="subdomain-refused"),
        pytest.param(None, None, False, id="no-origin"),
    ],
)
def test_public_channel_origin_rule(public_channel, origin, referer, allowed):
    assert channel_origin_allowed(_request(origin, referer), public_channel) is allowed


@pytest.mark.django_db()
def test_embedded_channel_still_uses_its_domain_list(experiment):
    channel = ExperimentChannelFactory.create(
        experiment=experiment,
        platform=ChannelPlatform.EMBEDDED_WIDGET,
        extra_data={"widget_token": TOKEN, "allowed_domains": ["partner.example.com"]},
    )
    assert channel_origin_allowed(_request("https://partner.example.com"), channel) is True
    assert channel_origin_allowed(_request(f"https://{CANONICAL}"), channel) is False


@pytest.mark.django_db()
def test_embed_key_authorizes_a_public_channel_from_the_canonical_origin(public_channel):
    request = _request(f"https://{CANONICAL}")
    request.META["HTTP_X_EMBED_KEY"] = TOKEN
    assert embed_key_authorizes_channel(request, public_channel) is True
    foreign = _request("https://evil.example.org")
    foreign.META["HTTP_X_EMBED_KEY"] = TOKEN
    assert embed_key_authorizes_channel(foreign, public_channel) is False


def _start(client, experiment, body=None, **extra):
    return client.post(
        reverse("api:chat:start-session"),
        data={"chatbot_id": experiment.public_id, "session_data": {"source": "widget"}, **(body or {})},
        format="json",
        **extra,
    )


@pytest.mark.django_db()
def test_anonymous_start_from_the_canonical_origin_lands_on_the_public_channel(public_channel):
    response = _start(
        APIClient(), public_channel.experiment, HTTP_X_EMBED_KEY=TOKEN, HTTP_ORIGIN=f"https://{CANONICAL}"
    )
    assert response.status_code == 201, response.content
    session = ExperimentSession.objects.get(external_id=response.json()["session_id"])
    assert session.experiment_channel == public_channel
    assert session.participant.platform == "public"


@pytest.mark.django_db()
def test_anonymous_start_from_a_foreign_origin_is_refused(public_channel):
    response = _start(
        APIClient(), public_channel.experiment, HTTP_X_EMBED_KEY=TOKEN, HTTP_ORIGIN="https://evil.example.org"
    )
    assert response.status_code == 403


@pytest.mark.django_db()
@pytest.mark.parametrize("member", [pytest.param(True, id="team-member"), pytest.param(False, id="non-member")])
def test_logged_in_user_on_the_page_lands_on_the_public_channel(public_channel, member):
    team = public_channel.team
    user = team.members.first() if member else UserFactory.create()
    client = APIClient()
    client.force_login(user)
    response = _start(
        client,
        public_channel.experiment,
        {"participant_remote_id": user.email},
        HTTP_X_EMBED_KEY=TOKEN,
        HTTP_ORIGIN=f"https://{CANONICAL}",
    )
    assert response.status_code == 201, response.content
    session = ExperimentSession.objects.get(external_id=response.json()["session_id"])
    assert session.experiment_channel == public_channel
