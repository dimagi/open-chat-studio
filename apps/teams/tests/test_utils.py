from django.utils.functional import SimpleLazyObject

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
    """This was downgraded to a log error instead of a ValueError until it has been verified in production"""
    team1 = Team(slug="team1", name="team1")
    team2 = Team(slug="team2", name="team2")

    assert get_current_team() is None

    set_current_team(team1)
    set_current_team(team2)
    assert get_current_team() == team2  # change is still done


def test_value_error_when_changing_teams_from_none_and_back():
    """Test that we do not trigger the validation when either the new or existing value is None"""
    team1 = Team(slug="team1", name="team1")

    assert get_current_team() is None

    set_current_team(team1)
    set_current_team(None)


def test_value_error_when_changing_teams_context_manager():
    team1 = Team(slug="team1", name="team1")
    team2 = Team(slug="team2", name="team2")

    assert get_current_team() is None

    set_current_team(team1)

    with current_team(team2):
        assert get_current_team() is team2

    # check that the team is reverted to the previous one
    assert get_current_team() is team1


def test_team_context_lazy_object():
    team1 = Team(slug="team1", name="team1")
    team2 = Team(slug="team2", name="team2")

    unset_current_team()  # set to None
    assert get_current_team() is None

    # test setting with `None` lazy object
    set_current_team(SimpleLazyObject(lambda: None))
    assert get_current_team() is None

    set_current_team(SimpleLazyObject(lambda: team1))
    assert get_current_team() is team1

    # setting it again is allowed
    set_current_team(SimpleLazyObject(lambda: team1))
    assert get_current_team() is team1

    set_current_team(SimpleLazyObject(lambda: team2))
    assert get_current_team() is team2


def test_team_context_lazy_object_context_manager_reentry():
    team1 = Team(slug="team1", name="team1")

    unset_current_team()  # set to None
    assert get_current_team() is None

    with current_team(SimpleLazyObject(lambda: team1)):
        assert get_current_team() is team1

        with current_team(SimpleLazyObject(lambda: team1)):
            assert get_current_team() is team1
