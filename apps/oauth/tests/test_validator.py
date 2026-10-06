from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

import pytest
from allauth.account.models import EmailAddress
from django.utils import timezone
from oauth2_provider.oauth2_validators import OAuth2Validator

from apps.oauth.models import OAuth2Application, OAuth2Grant
from apps.oauth.validator import APIScopedValidator
from apps.teams.utils import current_team
from apps.utils.factories.team import MembershipFactory, TeamFactory, TeamWithUsersFactory
from apps.utils.factories.user import UserFactory


@pytest.fixture()
def validator():
    return APIScopedValidator()


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("verified", "expected"),
    [
        pytest.param(True, True, id="confirmed-email"),
        pytest.param(False, False, id="unconfirmed-email"),
        pytest.param(None, False, id="no-email-address-record"),
    ],
)
def test_email_verified_claim_reflects_email_confirmation(validator, verified, expected):
    """email_verified mirrors whether the user's primary email address is confirmed.

    ``verified`` is the state of the EmailAddress record to create, or ``None`` to
    create no record at all.
    """
    user = UserFactory.create()
    if verified is not None:
        EmailAddress.objects.create(user=user, email=user.email, verified=verified, primary=True)
    request = SimpleNamespace(user=user)

    claims = validator.get_additional_claims(request)

    assert claims["sub"] == user.email
    assert claims["email_verified"] is expected


@pytest.mark.django_db()
def test_email_verified_claim_is_scoped_to_openid():
    """The email_verified claim is only emitted within the openid scope."""
    assert APIScopedValidator.oidc_claim_scope["email_verified"] == "openid"


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("scopes", "expect_teams"),
    [
        pytest.param(["openid", "teams"], True, id="teams-scope-granted"),
        pytest.param(["openid"], False, id="teams-scope-not-granted"),
    ],
)
def test_teams_claim_lists_the_users_teams(validator, scopes, expect_teams):
    """The teams claim lists every team the user is a member of, and only when the teams scope is granted."""
    user = UserFactory.create()
    teams = [membership.team for membership in MembershipFactory.create_batch(2, user=user)]
    TeamFactory.create()
    request = SimpleNamespace(user=user, scopes=scopes)

    claims = validator.get_oidc_claims(None, None, request)

    expected = [{"slug": team.slug, "name": team.name} for team in sorted(teams, key=lambda team: team.slug)]
    assert claims.get("teams") == (expected if expect_teams else None)


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("pin_application_to_team", "expected"),
    [
        pytest.param(True, False, id="team-scoped-application"),
        pytest.param(False, True, id="global-application"),
    ],
)
def test_only_global_applications_can_request_the_teams_scope(validator, settings, pin_application_to_team, expected):
    """A team-scoped application's tokens only reach its own team, so it may not list the user's other teams."""
    settings.OAUTH2_PROVIDER = settings.OAUTH2_PROVIDER | {
        "SCOPES": settings.OAUTH2_PROVIDER["SCOPES"] | settings.OIDC_ONLY_SCOPES
    }
    client = OAuth2Application(
        team=TeamFactory.create() if pin_application_to_team else None,
        authorization_grant_type=OAuth2Application.GRANT_AUTHORIZATION_CODE,
    )

    assert validator.validate_scopes("cid", ["openid", "teams"], client, None) is expected


@pytest.mark.django_db()
@pytest.mark.parametrize(
    ("pin_application_to_team", "expect_application_team"),
    [
        pytest.param(True, True, id="team-scoped-application"),
        pytest.param(False, False, id="global-application"),
    ],
)
def test_authorization_code_team(validator, pin_application_to_team, expect_application_team):
    """A team-scoped application's grant is for its own team, whatever the thread context says.

    The context is only consulted for global applications, where the authorizing user chose the team.
    This also covers the paths that skip the authorization form entirely (``approval_prompt=auto``).
    """
    application_team = TeamWithUsersFactory.create()
    context_team = TeamWithUsersFactory.create()
    application = OAuth2Application.objects.create(
        name="app",
        team=application_team if pin_application_to_team else None,
        client_type=OAuth2Application.CLIENT_CONFIDENTIAL,
        authorization_grant_type=OAuth2Application.GRANT_AUTHORIZATION_CODE,
        redirect_uris="https://example.com/callback",
    )
    grant = OAuth2Grant.objects.create(
        application=application,
        user=UserFactory.create(),
        code="code",
        expires=timezone.now() + timedelta(minutes=5),
        redirect_uri="https://example.com/callback",
    )
    with (
        current_team(context_team),
        mock.patch.object(OAuth2Validator, "_create_authorization_code", return_value=grant),
    ):
        result = validator._create_authorization_code(SimpleNamespace(client=application), "code")

    assert result.team == (application_team if expect_application_team else context_team)
