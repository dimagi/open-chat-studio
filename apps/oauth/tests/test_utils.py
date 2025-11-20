import pytest

from apps.oauth.utils import extract_team_scopes


@pytest.mark.parametrize(
    "scopes,expected",
    [
        (["team:my-team", "read", "write"], ["my-team"]),
        (["team:team-one", "read", "team:team-two", "write"], ["team-one", "team-two"]),
        (["read", "write", "delete"], []),
        (["team:my_team_name", "read"], ["my_team_name"]),
        (["team:my-team-name", "read"], ["my-team-name"]),
        (["team:team123", "read"], ["team123"]),
        (["team:", "team:UPPERCASE", "team:with spaces", "team:special!chars", "read"], []),
        ([], []),
        (["team:valid-team", "team:INVALID", "team:another-valid", "notateam:fake"], ["valid-team", "another-valid"]),
    ],
)
def test_extract_team_scopes(scopes, expected):
    """Test extracting team scopes with various formats and edge cases."""
    result = extract_team_scopes(scopes)
    assert result == expected
