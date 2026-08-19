"""The OAuth credential at the chat door.

A chatbot is unreachable over the chat API until an admin exposes it, and the Chat API Channel's
`credential_mode` says what a caller must present. These tests cover the third credential -- a
client-credentials token -- and the interactions between it and the two that already ship.

The membership and embed-key rows are ADR-0053's and live in `test_chat_api_authed.py`; the
allowlist's own semantics are ADR-0056's and live in `test_application_chatbot_allowlist.py`. What
follows tests the *chat door*: which credentials it admits, and that every refusal looks the same.
"""

import uuid
from datetime import timedelta

import pytest
import time_machine
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.api.authentication import ChatOAuthAuthentication
from apps.api.views.chat import START_AUTH_CLASSES
from apps.channels.models import ChannelPlatform, CredentialMode, WidgetAuthLevel
from apps.experiments.models import ExperimentSession
from apps.oauth.models import OAuth2AccessToken, OAuth2Application
from apps.teams.backends import add_user_to_team
from apps.utils.factories.channels import ExperimentChannelFactory
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamFactory
from apps.utils.factories.user import UserFactory
from apps.utils.tests.clients import ApiTestClient

EMBED_KEY = "test_widget_token_123456789012"
ORIGIN = "https://example.com"
DENIED = {"error": "Authentication required to chat with this chatbot", "code": "chat_access_denied"}


def _channel(experiment, mode=CredentialMode.OAUTH, allowed_domains=("example.com",)):
    return ExperimentChannelFactory.create(
        team=experiment.team,
        experiment=experiment,
        platform=ChannelPlatform.EMBEDDED_WIDGET,
        credential_mode=mode,
        required_auth_level=WidgetAuthLevel.SESSION_TOKEN,
        extra_data={"widget_token": EMBED_KEY, "allowed_domains": list(allowed_domains)},
    )


def _machine_client(team, allowed_chatbots=None, scopes=("chat:start",)):
    """A machine client. Its application owner is deliberately not a team member: admission must
    never fall back to a membership row.
    """
    return ApiTestClient(
        UserFactory.create(),
        team,
        auth_method="oauth_client_credentials",
        scopes=list(scopes),
        allowed_chatbots=allowed_chatbots,
    )


def _start(client, chatbot_id, origin=ORIGIN, **headers):
    if origin is not None:
        headers["HTTP_ORIGIN"] = origin
    return client.post(
        reverse("api:chat:start-session"),
        data={"chatbot_id": str(chatbot_id), "session_data": {"source": "widget"}},
        format="json",
        **headers,
    )


@pytest.fixture()
def chatbot(db):
    return ExperimentFactory.create(team=TeamFactory.create())


def test_oauth_authenticator_is_first():
    """D6's 401 depends on this position: DRF reads `authenticators[0].authenticate_header()` and
    coerces to 403 when it returns None. A reorder would silently reintroduce 403s -- and would
    also let a still-present X-Embed-Key match before the token is ever validated.
    """
    assert START_AUTH_CLASSES[0] is ChatOAuthAuthentication


@pytest.mark.django_db()
def test_machine_token_starts_a_session_on_an_oauth_channel(chatbot):
    channel = _channel(chatbot)
    client = _machine_client(chatbot.team, allowed_chatbots=[chatbot])

    response = _start(client, chatbot.public_id)

    assert response.status_code == 201, response.json()
    session = ExperimentSession.objects.get(experiment_channel=channel)
    assert session.external_id == response.json()["session_id"]
    # The mode pins SESSION_TOKEN, so a token must come back or every follow-up call 403s.
    assert response.json()["session_token"]


@pytest.mark.django_db()
def test_server_integration_needs_no_origin(chatbot):
    """A blank domain list declares the channel server-only, which is the honest configuration for
    a machine integration -- and avoids making an admin tick "allow all domains" to get it.
    """
    _channel(chatbot, allowed_domains=())
    client = _machine_client(chatbot.team, allowed_chatbots=[chatbot])

    assert _start(client, chatbot.public_id, origin=None).status_code == 201


@pytest.mark.django_db()
def test_machine_token_refused_when_the_channel_is_in_embed_key_mode(chatbot):
    """The mode is the enablement: a valid token is not enough on its own."""
    _channel(chatbot, mode=CredentialMode.EMBED_KEY)
    client = _machine_client(chatbot.team, allowed_chatbots=[chatbot])

    response = _start(client, chatbot.public_id)

    assert response.status_code == 401
    assert response.json() == DENIED


@pytest.mark.django_db()
def test_machine_token_refused_when_the_chatbot_has_no_chat_api_channel(chatbot):
    """The channel is the enablement. No channel, no OAuth admission."""
    client = _machine_client(chatbot.team, allowed_chatbots=[chatbot])

    response = _start(client, chatbot.public_id)

    assert response.status_code == 401
    assert response.json() == DENIED


@pytest.mark.django_db()
def test_machine_token_for_another_team_is_refused(chatbot):
    _channel(chatbot)
    other_team = TeamFactory.create()
    client = _machine_client(other_team, allowed_chatbots=[chatbot])

    assert _start(client, chatbot.public_id).status_code == 401


@pytest.mark.django_db()
def test_machine_token_whose_application_does_not_list_the_chatbot_is_refused(chatbot):
    """A 401 here rather than ADR-0056's 403: at this door the allowlist is one admission check
    among several, and the response must not say which one failed.
    """
    _channel(chatbot)
    other = ExperimentFactory.create(team=chatbot.team)
    client = _machine_client(chatbot.team, allowed_chatbots=[other])

    response = _start(client, chatbot.public_id)

    assert response.status_code == 401
    assert response.json() == DENIED


@pytest.mark.django_db()
def test_empty_allowlist_authorises_nothing(chatbot):
    _channel(chatbot)
    client = _machine_client(chatbot.team, allowed_chatbots=[])

    assert _start(client, chatbot.public_id).status_code == 401


@pytest.mark.django_db()
def test_token_for_one_chatbot_cannot_be_replayed_against_another(chatbot):
    """The leak-a-page-token case: `chat:start` is team-scoped, so the allowlist is the per-chatbot
    pin that the dropped embed-key-*and*-token mode used to provide.
    """
    _channel(chatbot)
    sibling = ExperimentFactory.create(team=chatbot.team)
    _channel(sibling)
    client = _machine_client(chatbot.team, allowed_chatbots=[chatbot])

    assert _start(client, chatbot.public_id).status_code == 201
    assert _start(client, sibling.public_id).status_code == 401


@pytest.mark.django_db()
def test_addressing_a_version_by_its_own_public_id_is_a_404(chatbot):
    """This door resolves working versions only, so the lookup fails before any credential is
    examined. Version addressing lives on the chatbots:interact surfaces, not here.
    """
    _channel(chatbot)
    version = chatbot.create_new_version()
    client = _machine_client(chatbot.team, allowed_chatbots=[chatbot])

    assert _start(client, version.public_id).status_code == 404


@pytest.mark.django_db()
def test_unknown_chatbot_id_is_a_404_not_a_401(chatbot):
    """Chatbot existence is not a secret -- `public_id`s ship in every embed snippet -- and the 404
    is the only signal that tells an integrator with a typo what is wrong.
    """
    client = _machine_client(chatbot.team, allowed_chatbots=[chatbot])

    assert _start(client, uuid.uuid4()).status_code == 404


@pytest.mark.django_db()
def test_chatbots_interact_scope_does_not_admit(chatbot):
    """The narrow scope is mandatory: a host cannot reach for the broad token it already has, so
    the token it puts in a page is narrow by construction.
    """
    _channel(chatbot)
    client = _machine_client(chatbot.team, allowed_chatbots=[chatbot], scopes=("chatbots:interact",))

    response = _start(client, chatbot.public_id)

    assert response.status_code == 401
    assert response.json() == DENIED


@pytest.mark.django_db()
def test_expired_token_is_refused(chatbot):
    _channel(chatbot)
    client = _machine_client(chatbot.team, allowed_chatbots=[chatbot])
    OAuth2AccessToken.objects.filter(token=client._token).update(expires=timezone.now() - timedelta(minutes=1))

    assert _start(client, chatbot.public_id).status_code == 401


@pytest.mark.django_db()
def test_revoked_token_is_refused(chatbot):
    _channel(chatbot)
    client = _machine_client(chatbot.team, allowed_chatbots=[chatbot])
    OAuth2AccessToken.objects.filter(token=client._token).delete()

    assert _start(client, chatbot.public_id).status_code == 401


@pytest.mark.django_db()
def test_authorization_code_token_is_refused(chatbot):
    """Their team comes from a Grant plus a live membership check, which raises a question this
    door does not need to answer: may a signed-in user's token chat as an anonymous participant?
    """
    _channel(chatbot)
    user = UserFactory.create()
    application = OAuth2Application.objects.create(
        name="user-app",
        user=user,
        team=chatbot.team,
        client_type=OAuth2Application.CLIENT_CONFIDENTIAL,
        authorization_grant_type=OAuth2Application.GRANT_AUTHORIZATION_CODE,
        redirect_uris="https://example.com/callback",
    )
    application.allowed_chatbots.set([chatbot])
    token = OAuth2AccessToken.objects.create(
        application=application,
        user=user,
        team=chatbot.team,
        expires=timezone.now() + timedelta(days=1),
        token="user-token",
        scope="chat:start",
    )

    client = APIClient()
    response = _start(client, chatbot.public_id, HTTP_AUTHORIZATION=f"Bearer {token.token}")

    assert response.status_code == 401
    assert response.json() == DENIED


@pytest.mark.django_db()
def test_a_stale_embed_key_riding_along_with_a_valid_token_is_ignored(chatbot):
    """In `oauth` mode the key is ignored, not rejected: an existing snippet keeps sending it and
    keeps working once `auth-token` is added.
    """
    _channel(chatbot)
    client = _machine_client(chatbot.team, allowed_chatbots=[chatbot])

    response = _start(client, chatbot.public_id, HTTP_X_EMBED_KEY="stale_key_000000000000000000")

    assert response.status_code == 201


@pytest.mark.django_db()
def test_embed_key_alone_does_not_admit_on_an_oauth_channel(chatbot):
    """Ignored means "not required and not rejected", not "sufficient". This is the only place an
    `oauth`-mode channel reached with a key and no token can fail.
    """
    _channel(chatbot)

    response = _start(APIClient(), chatbot.public_id, HTTP_X_EMBED_KEY=EMBED_KEY)

    assert response.status_code == 401
    assert response.json() == DENIED


@pytest.mark.django_db()
def test_a_logged_in_non_member_with_the_embed_key_does_not_admit_either(chatbot, client):
    """ADR-0053 lets an embed key stand in for membership. The mode withdraws that on an `oauth`
    channel, so a leaked key plus any OCS login is not a way in.
    """
    _channel(chatbot)
    client.force_login(UserFactory.create())

    response = client.post(
        reverse("api:chat:start-session"),
        data={"chatbot_id": str(chatbot.public_id), "session_data": {"source": "widget"}},
        content_type="application/json",
        HTTP_ORIGIN=ORIGIN,
        HTTP_X_EMBED_KEY=EMBED_KEY,
    )

    # 403, not 401: a 401 at an authenticated caller reads as a broken session and invites a
    # pointless re-login.
    assert response.status_code == 403
    assert response.json() == {"error": "You do not have access to this chatbot"}


@pytest.mark.django_db()
def test_a_team_member_still_gets_in_without_a_key_or_token(chatbot, client):
    """Switching a channel to `oauth` must not lock team members out of their own in-app embeds:
    the mode is evaluated on the embed-key branch, never on the membership branch.
    """
    _channel(chatbot)
    member = UserFactory.create()
    add_user_to_team(chatbot.team, member)
    client.force_login(member)

    response = client.post(
        reverse("api:chat:start-session"),
        data={
            "chatbot_id": str(chatbot.public_id),
            "session_data": {"source": "widget"},
            "participant_remote_id": member.email,
        },
        content_type="application/json",
        HTTP_ORIGIN=ORIGIN,
    )

    assert response.status_code == 201, response.json()


@pytest.mark.django_db()
def test_machine_token_may_not_select_a_version(chatbot):
    """Version selection stays a member-only capability (ADR-0053), so this is a 403 like every
    other unauthenticated attempt at it -- not one of the door's uniform 401s.
    """
    _channel(chatbot)
    chatbot.create_new_version()
    client = _machine_client(chatbot.team, allowed_chatbots=[chatbot])

    response = client.post(
        reverse("api:chat:start-session"),
        data={"chatbot_id": str(chatbot.public_id), "version_number": 1},
        format="json",
        HTTP_ORIGIN=ORIGIN,
    )

    assert response.status_code == 403
    assert response.json() == {"error": "Version number requires authentication"}


@pytest.mark.django_db()
def test_an_invalid_token_never_falls_through_to_the_keyless_path(chatbot):
    """`OAuth2Authentication` returns None for an invalid token exactly as it does for no token at
    all. Reading that as "no credential was offered" would admit a caller whose token OCS had just
    revoked, via the still-open keyless path.
    """
    _channel(chatbot, mode=CredentialMode.EMBED_KEY)
    client = APIClient()

    response = _start(client, chatbot.public_id, HTTP_AUTHORIZATION="Bearer not-a-real-token")

    assert response.status_code == 401
    assert response.json() == DENIED


@pytest.mark.django_db()
def test_the_keyless_path_is_untouched(chatbot):
    """Deny-by-default is the destination, not the state: closing this is the sunset's work."""
    _channel(chatbot, mode=CredentialMode.EMBED_KEY)

    assert _start(APIClient(), chatbot.public_id).status_code == 201


@pytest.mark.django_db()
def test_the_session_a_token_opens_can_be_used_with_its_session_token(chatbot):
    """The mode/level invariant, end to end. This is the failure the CheckConstraint exists to
    prevent: an `oauth` channel below SESSION_TOKEN issues no token, and then every follow-up call
    lands in `_has_legacy_access` and 403s -- a session dead on arrival.
    """
    _channel(chatbot)
    client = _machine_client(chatbot.team, allowed_chatbots=[chatbot])

    body = _start(client, chatbot.public_id).json()

    poll = APIClient().get(
        reverse("api:chat:poll-response", kwargs={"session_id": body["session_id"]}),
        HTTP_X_SESSION_TOKEN=body["session_token"],
    )
    assert poll.status_code == 200


@pytest.mark.django_db()
def test_a_session_that_ages_out_is_re_admitted_only_with_a_live_token(chatbot):
    """The point of the bounded lifetime (D7). The widget's `session_expired` recovery calls
    `chat/start/` again, which re-crosses the OAuth gate -- so whatever check the host puts in front
    of its own token-minting endpoint runs again at the cadence of the lifetime. A restart admitted
    without re-checking the credential would make the bound worth nothing.
    """
    channel = _channel(chatbot)
    channel.session_token_lifetime = timedelta(hours=4)
    channel.save()
    client = _machine_client(chatbot.team, allowed_chatbots=[chatbot])
    first = _start(client, chatbot.public_id).json()

    with time_machine.travel(timezone.now() + timedelta(hours=5)):
        expired = APIClient().get(
            reverse("api:chat:poll-response", kwargs={"session_id": first["session_id"]}),
            HTTP_X_SESSION_TOKEN=first["session_token"],
        )
        assert expired.status_code == 403
        assert expired.json()["code"] == "session_expired"

        # The page's own bearer token has aged out too: the host must push a fresh one.
        OAuth2AccessToken.objects.filter(token=client._token).update(expires=timezone.now() - timedelta(minutes=1))
        assert _start(client, chatbot.public_id).status_code == 401

        OAuth2AccessToken.objects.filter(token=client._token).update(expires=timezone.now() + timedelta(hours=1))
        restarted = _start(client, chatbot.public_id)

    assert restarted.status_code == 201
    assert restarted.json()["session_id"] != first["session_id"]


@pytest.mark.django_db()
def test_the_cross_origin_preflight_allows_the_authorization_header():
    """Without this the browser rejects an `oauth`-mode embed before the view ever runs. Safe:
    CORS_URLS_REGEX limits CORS to the chat API and CORS_ALLOW_CREDENTIALS is False, so allowing the
    header only permits what the page's own JS sets -- no ambient credentials.
    """
    response = APIClient().options(
        reverse("api:chat:start-session"),
        HTTP_ORIGIN=ORIGIN,
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS="authorization",
    )

    assert "authorization" in response["access-control-allow-headers"]
