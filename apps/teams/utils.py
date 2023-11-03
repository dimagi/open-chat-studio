from contextvars import ContextVar

_context = ContextVar("current_team")


def get_current_team():
    """
    Utils to get the team that has been set in the current thread/context using `set_current_team`.
    Can be used by doing:
    ```
        team = get_current_team()
    ```
    Will return None if the team is not set
    """
    return getattr(_context, "team", None)


def set_current_team(team):
    """
    Utils to set a team in the current thread/context.
    Used in a middleware once a user is logged in.
    Can be used by doing:
    ```
        get_current_team(team)
    ```
    """
    setattr(_context, "team", team)


def unset_current_team():
    setattr(_context, "team", None)
