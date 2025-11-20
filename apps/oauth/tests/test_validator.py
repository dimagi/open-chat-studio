from unittest.mock import Mock

import pytest

from apps.oauth.validator import TeamScopedOAuth2Validator
from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture()
def validator():
    return TeamScopedOAuth2Validator()


@pytest.fixture()
def user_with_teams(db):
    team_with_users = TeamWithUsersFactory.create()
    user = team_with_users.members.first()
    return user, team_with_users


@pytest.mark.django_db()
@pytest.mark.parametrize(
    "scenario",
    [
        "valid_team",
        "invalid_team",
        "multiple_teams",
        "discrepancy",
        "no_team_scopes",
    ],
)
def test_validate_scopes(validator, user_with_teams, scenario):
    """Test OAuth2 validator scopes with various team configurations."""
    user, team = user_with_teams
    request = Mock()
    request.user = user

    if scenario == "valid_team":
        scopes = [f"team:{team.slug}"]
        expected = True
    elif scenario == "invalid_team":
        other_team = TeamWithUsersFactory.create()
        scopes = [f"team:{other_team.slug}"]
        expected = False
    else:
        scopes = []
        expected = True

    result = validator.validate_scopes(client_id="test_client", scopes=scopes, client=None, request=request)

    assert result is expected
