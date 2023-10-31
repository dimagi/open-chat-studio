import pytest

from apps.utils.factories.team import TeamWithUsersFactory


@pytest.fixture
def team(db):
    return TeamWithUsersFactory.create()
