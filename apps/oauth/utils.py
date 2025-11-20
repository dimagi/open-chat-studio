import re


def extract_team_scopes(scopes):
    """Extract team slugs from scopes."""
    team_pattern = re.compile(r"^team:([a-z0-9-_]+)$")
    team_scopes = []

    for scope in scopes:
        match = team_pattern.match(scope)
        if match:
            team_scopes.append(match.group(1))

    return team_scopes
