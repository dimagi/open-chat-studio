"""The origin rule at the chat door, mode x origin x domain list.

Each credential validates its own origin, so the rule differs by which credential is presented --
but only on requests with no `Origin`/`Referer`. The *domain list* is what says whether a channel is
browser-facing: an admin already declares that by filling the list or leaving it empty, so nothing
extra distinguishes a server integration from an embed.

The `oauth` + non-blank + originless rejection is what keeps the collapse to two credential modes
safe: the dropped embed-key-*and*-token mode inherited "reject an originless request" from the embed
key, and keying it on the domain list recovers the same protection -- a token leaked from a page
cannot be replayed from `curl` against a browser-facing channel.
"""

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.channels.models import ChannelPlatform, CredentialMode, WidgetAuthLevel
from apps.channels.utils import ALL_DOMAINS
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.user import UserFactory
from apps.utils.tests.clients import ApiTestClient

EMBED_KEY = "test_widget_token_123456789012"


@pytest.fixture()
def chatbot(db):
    return ExperimentFactory.create(team=TeamFactory.create())


def _channel(experiment, mode, allowed_domains):
    return ExperimentChannelFactory.create(
        team=experiment.team,
        experiment=experiment,
        platform=ChannelPlatform.EMBEDDED_WIDGET,
        credential_mode=mode,
        required_auth_level=(
            WidgetAuthLevel.SESSION_TOKEN if mode == CredentialMode.OAUTH else WidgetAuthLevel.EMBED_KEY
        ),
        extra_data={"widget_token": EMBED_KEY, "allowed_domains": list(allowed_domains)},
    )


def _post(client, chatbot, origin, **headers):
    if origin is not None:
        headers["HTTP_ORIGIN"] = origin
    return client.post(
        reverse("api:chat:start-session"),
        data={"chatbot_id": str(chatbot.public_id), "session_data": {"source": "widget"}},
        format="json",
        **headers,
    )


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        pytest.param(None, 403, id="origin_absent_rejected"),
        pytest.param("https://example.com", 201, id="origin_matches"),
        pytest.param("https://elsewhere.com", 403, id="origin_does_not_match"),
    ],
)
def test_embed_key_mode_is_unchanged(chatbot, origin, expected):
    """An embed key with no Origin is the abuse case -- a stolen key used from `curl`."""
    _channel(chatbot, CredentialMode.EMBED_KEY, ["example.com"])

    response = _post(APIClient(), chatbot, origin, HTTP_X_EMBED_KEY=EMBED_KEY)

    assert response.status_code == expected


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("allowed_domains", "origin", "expected"),
    [
        # A blank list means server-only: the honest configuration for a machine integration, and
        # it avoids making an admin tick "allow all domains" to get it.
        pytest.param([], None, 201, id="blank_list_no_origin_is_the_server_integration"),
        pytest.param([], "https://example.com", 401, id="blank_list_refuses_any_browser"),
        # A non-blank list declares the channel browser-facing, so an originless request is refused
        # exactly as it is under embed_key.
        pytest.param(["example.com"], None, 401, id="listed_refuses_originless"),
        pytest.param(["example.com"], "https://example.com", 201, id="listed_admits_a_match"),
        pytest.param(["example.com"], "https://elsewhere.com", 401, id="listed_refuses_a_mismatch"),
        pytest.param([ALL_DOMAINS], None, 401, id="allow_all_still_refuses_originless"),
        pytest.param([ALL_DOMAINS], "https://anywhere.com", 201, id="allow_all_admits_any_origin"),
    ],
)
def test_oauth_mode_follows_the_domain_list(chatbot, allowed_domains, origin, expected):
    _channel(chatbot, CredentialMode.OAUTH, allowed_domains)
    client = ApiTestClient(
        UserFactory.create(),
        chatbot.team,
        auth_method="oauth_client_credentials",
        scopes=["chat:start"],
        allowed_chatbots=[chatbot],
    )

    response = _post(client, chatbot, origin)

    assert response.status_code == expected, response.json()


@pytest.mark.django_db()
def test_a_volunteered_referer_is_judged_by_it(chatbot):
    """`extract_domain_from_headers` reads Origin *or* Referer, so a non-browser client that sends a
    Referer is treated as a browser request -- and refused by a blank list. Worth documenting; not
    worth special-casing.
    """
    _channel(chatbot, CredentialMode.OAUTH, [])
    client = ApiTestClient(
        UserFactory.create(),
        chatbot.team,
        auth_method="oauth_client_credentials",
        scopes=["chat:start"],
        allowed_chatbots=[chatbot],
    )

    response = _post(client, chatbot, None, HTTP_REFERER="https://example.com/page")

    assert response.status_code == 401
