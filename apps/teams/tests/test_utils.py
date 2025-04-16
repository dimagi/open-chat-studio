import pytest

from apps.teams.models import Team
from apps.teams.utils import current_team, get_current_team, set_current_team, unset_current_team


def test_using_context_manager_reverts_team():
    team1 = Team(slug="team1", name="team1")

    set_current_team(team1)
    with current_team(team1):
        assert get_current_team() == team1

    assert get_current_team() is team1
    unset_current_team()
    assert get_current_team() is None


def test_value_error_when_changing_teams():
    team1 = Team(slug="team1", name="team1")
    team2 = Team(slug="team2", name="team2")

    set_current_team(team1)
    with pytest.raises(ValueError, match="Cannot set a different team in the current context"):
        set_current_team(team2)

    with pytest.raises(ValueError, match="Cannot set a different team in the current context"):
        with current_team(team2):
            pass

    set_current_team(team1)
