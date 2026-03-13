import pytest
from django.test import Client

from apps.teams.backends import get_team_owner_groups
from apps.utils.factories.team import MembershipFactory, TeamFactory
from apps.utils.factories.user import UserFactory


@pytest.fixture()
def user():
    return UserFactory.create()


@pytest.fixture()
def team(user):
    team = TeamFactory.create()
    MembershipFactory.create(team=team, user=user, groups=get_team_owner_groups)
    return team


@pytest.fixture()
def anon_client():
    """Unauthenticated Django test client (intentionally shadows pytest-django's built-in)."""
    return Client()
