from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed

from apps.api.exceptions import ChatApiAccessDenied
from apps.oauth.models import OAuth2AccessToken, OAuth2Application
from apps.oauth.permissions import (
    OAuth2AccessTokenAuthentication,
    application_allows_chatbot,
    applications_allowing_chatbot,
    is_client_credentials_request,
    is_client_credentials_token,
    token_allows_chatbot,
    validated_machine_token,
)
from apps.teams.helpers import SyntheticTeamMembership
from apps.utils.factories.experiment import ExperimentFactory
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def team(db):
    return TeamWithUsersFactory.create()


@pytest.fixture()
def client_credentials_token(team):
    app = OAuth2Application.objects.create(
        name="machine-app",
        team=team,
        client_type=OAuth2Application.CLIENT_CONFIDENTIAL,
        authorization_grant_type=OAuth2Application.GRANT_CLIENT_CREDENTIALS,
    )
    return OAuth2AccessToken.objects.create(
        application=app,
        team=team,
        token="machine-token",
        scope="sessions:read",
        expires=timezone.now() + timedelta(days=1),
    )


@pytest.mark.django_db()
def test_is_client_credentials_request(client_credentials_token):
    assert is_client_credentials_request(SimpleNamespace(auth=client_credentials_token)) is True


@pytest.mark.django_db()
def test_is_client_credentials_request_false_for_non_oauth():
    assert is_client_credentials_request(SimpleNamespace(auth=object())) is False
    assert is_client_credentials_request(SimpleNamespace(auth=None)) is False


@pytest.mark.django_db()
def test_authenticate_client_credentials_sets_synthetic_identity(rf, client_credentials_token, team):
    """A machine token yields an AnonymousUser + SyntheticTeamMembership, with no membership gate."""
    request = rf.get("/api/sessions/")
    request.META["HTTP_AUTHORIZATION"] = f"Bearer {client_credentials_token.token}"

    user, token = OAuth2AccessTokenAuthentication().authenticate(request)

    assert isinstance(user, AnonymousUser)
    assert request.team.id == team.id
    assert isinstance(request.team_membership, SyntheticTeamMembership)
    assert request.team_membership.is_team_admin() is False


@pytest.mark.django_db()
def test_authenticate_authorization_code_still_requires_membership(rf, team):
    """Regression: an authorization-code token for a user with no membership row is rejected."""
    non_member = TeamWithUsersFactory.create().members.first()
    app = OAuth2Application.objects.create(
        name="auth-code-app",
        client_type=OAuth2Application.CLIENT_CONFIDENTIAL,
        authorization_grant_type=OAuth2Application.GRANT_AUTHORIZATION_CODE,
        redirect_uris="https://example.com/callback",
    )
    token = OAuth2AccessToken.objects.create(
        user=non_member,
        application=app,
        team=team,
        token="auth-code-token",
        scope="sessions:read",
        expires=timezone.now() + timedelta(days=1),
    )
    request = rf.get("/api/sessions/")
    request.META["HTTP_AUTHORIZATION"] = f"Bearer {token.token}"

    with pytest.raises(AuthenticationFailed):
        OAuth2AccessTokenAuthentication().authenticate(request)


@pytest.mark.django_db()
def test_application_allows_chatbot_only_when_listed(client_credentials_token, team):
    request = SimpleNamespace(auth=client_credentials_token)
    listed = ExperimentFactory.create(team=team)
    unlisted = ExperimentFactory.create(team=team)

    assert application_allows_chatbot(request, listed) is False

    client_credentials_token.application.allowed_chatbots.add(listed)

    assert application_allows_chatbot(request, listed) is True
    assert application_allows_chatbot(request, unlisted) is False


@pytest.mark.django_db()
def test_application_allows_chatbot_normalises_versions(client_credentials_token, team):
    """The allowlist holds family heads, but a caller can address a version by its own public_id."""
    request = SimpleNamespace(auth=client_credentials_token)
    chatbot = ExperimentFactory.create(team=team)
    version = chatbot.create_new_version()
    client_credentials_token.application.allowed_chatbots.add(chatbot)

    assert application_allows_chatbot(request, version) is True


@pytest.mark.django_db()
def test_application_allows_chatbot_ignores_non_machine_callers(team):
    """Only client-credentials applications are pinned; every other caller keeps team semantics."""
    chatbot = ExperimentFactory.create(team=team)

    assert application_allows_chatbot(SimpleNamespace(auth=None), chatbot) is True
    assert application_allows_chatbot(SimpleNamespace(auth=object()), chatbot) is True


@pytest.mark.django_db()
def test_token_shaped_helpers_match_their_request_wrappers(client_credentials_token, team):
    """The token-shaped cores exist because an authentication class resolves the token inside
    `authenticate()`, before `request.auth` exists -- so the request-shaped helpers cannot be called
    from there. Extracting them keeps the version normalisation in one place rather than
    reimplementing it at the chat door.
    """
    chatbot = ExperimentFactory.create(team=team)
    client_credentials_token.application.allowed_chatbots.add(chatbot)

    assert is_client_credentials_token(client_credentials_token) is True
    assert is_client_credentials_token(None) is False
    assert token_allows_chatbot(client_credentials_token, chatbot) is True
    assert token_allows_chatbot(client_credentials_token, ExperimentFactory.create(team=team)) is False


@pytest.mark.django_db()
def test_applications_allowing_chatbot_is_the_allowlist_read_backwards(client_credentials_token, team):
    """The channel dialog asks the question from the chatbot's side: which applications could mint
    a token that this channel would admit?"""
    chatbot = ExperimentFactory.create(team=team)
    application = client_credentials_token.application

    assert list(applications_allowing_chatbot(chatbot)) == []

    application.allowed_chatbots.add(chatbot)
    assert list(applications_allowing_chatbot(chatbot)) == [application]
    assert list(applications_allowing_chatbot(ExperimentFactory.create(team=team))) == []


@pytest.mark.django_db()
def test_applications_allowing_chatbot_normalises_a_version_to_its_working_copy(client_credentials_token, team):
    """The allowlist holds working versions, but a channel can hang off a released version."""
    chatbot = ExperimentFactory.create(team=team)
    client_credentials_token.application.allowed_chatbots.add(chatbot)
    version = chatbot.create_new_version()

    assert list(applications_allowing_chatbot(version)) == [client_credentials_token.application]


@pytest.mark.django_db()
def test_applications_allowing_chatbot_excludes_other_grants_and_other_teams(client_credentials_token, team):
    """Only client-credentials applications are pinned to a set of chatbots, and an application in
    another team could not reach this chatbot whatever its allowlist says."""
    chatbot = ExperimentFactory.create(team=team)
    client_credentials_token.application.allowed_chatbots.add(chatbot)

    authorization_code_app = OAuth2Application.objects.create(
        name="human-app",
        team=team,
        client_type=OAuth2Application.CLIENT_CONFIDENTIAL,
        authorization_grant_type=OAuth2Application.GRANT_AUTHORIZATION_CODE,
        redirect_uris="https://example.com/callback",
    )
    authorization_code_app.allowed_chatbots.add(chatbot)

    other_team_app = OAuth2Application.objects.create(
        name="other-team-app",
        team=TeamWithUsersFactory.create(),
        client_type=OAuth2Application.CLIENT_CONFIDENTIAL,
        authorization_grant_type=OAuth2Application.GRANT_CLIENT_CREDENTIALS,
    )
    other_team_app.allowed_chatbots.add(chatbot)

    assert list(applications_allowing_chatbot(chatbot)) == [client_credentials_token.application]


def _chat_request(rf, token, experiment):
    request = rf.post("/api/chat/start/", {"chatbot_id": str(experiment.public_id)})
    if token is not None:
        request.META["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    return request


@pytest.mark.django_db()
def test_validated_machine_token_accepts_a_scoped_listed_token(rf, client_credentials_token, team):
    chatbot = ExperimentFactory.create(team=team)
    client_credentials_token.application.allowed_chatbots.add(chatbot)
    client_credentials_token.scope = settings.CHAT_API_SCOPE
    client_credentials_token.save()

    token = validated_machine_token(_chat_request(rf, client_credentials_token.token, chatbot), chatbot)

    assert token.pk == client_credentials_token.pk


@pytest.mark.django_db()
def test_validated_machine_token_refuses_a_token_that_does_not_check_out(rf, client_credentials_token, team):
    """An invalid token raises rather than returning None. `OAuth2Authentication` returns None for an
    invalid or expired token exactly as it does for no token at all, and treating that as "no
    credential was offered" would let a revoked token fall through to the next authenticator and
    from there to the still-open keyless path.
    """
    chatbot = ExperimentFactory.create(team=team)
    client_credentials_token.application.allowed_chatbots.add(chatbot)
    client_credentials_token.scope = settings.CHAT_API_SCOPE
    client_credentials_token.save()

    with pytest.raises(ChatApiAccessDenied):
        validated_machine_token(_chat_request(rf, "not-a-real-token", chatbot), chatbot)


@pytest.mark.django_db()
def test_validated_machine_token_refuses_a_token_pinned_to_another_team(rf, client_credentials_token):
    other_chatbot = ExperimentFactory.create(team=TeamWithUsersFactory.create())
    client_credentials_token.application.allowed_chatbots.add(other_chatbot)
    client_credentials_token.scope = settings.CHAT_API_SCOPE
    client_credentials_token.save()

    with pytest.raises(ChatApiAccessDenied):
        validated_machine_token(_chat_request(rf, client_credentials_token.token, other_chatbot), other_chatbot)


@pytest.mark.django_db()
def test_validated_machine_token_requires_the_narrow_scope(rf, client_credentials_token, team):
    """chatbots:interact also converses with every chatbot in the team and sends outbound
    WhatsApp/Telegram messages to arbitrary participants -- the wrong credential to hand a browser.
    """
    chatbot = ExperimentFactory.create(team=team)
    client_credentials_token.application.allowed_chatbots.add(chatbot)
    client_credentials_token.scope = "chatbots:interact"
    client_credentials_token.save()

    with pytest.raises(ChatApiAccessDenied):
        validated_machine_token(_chat_request(rf, client_credentials_token.token, chatbot), chatbot)
