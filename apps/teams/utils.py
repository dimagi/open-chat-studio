from contextlib import contextmanager
from contextvars import ContextVar

_context = ContextVar("team")


def get_current_team():
    """
    Utils to get the team that has been set in the current thread/context using `set_current_team`.
    Can be used by doing:
    ```
        team = get_current_team()
    ```
    Will return None if the team is not set
    """
    try:
        return _context.get()
    except LookupError:
        pass


def set_current_team(team):
    """
    Utils to set a team in the current thread/context.
    Used in a middleware once a user is logged in.
    Can be used by doing:
    ```
        get_current_team(team)
    ```
    """
    _context.set(team)


def unset_current_team():
    _context.set(None)


@contextmanager
def current_team(team):
    """Context manager useful for testing."""
    set_current_team(team)
    try:
        yield
    finally:
        unset_current_team()
