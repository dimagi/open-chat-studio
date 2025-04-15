from apps.teams.models import Team
from apps.teams.utils import current_team, get_current_team, set_current_team


def test_using_context_manager_reverts_team():
    team1 = Team(slug="team1", name="team1")
    team2 = Team(slug="team2", name="team2")

    assert get_current_team() is None
    set_current_team(team1)

    with current_team(team2):
        assert get_current_team() == team2
    assert get_current_team() is team1
