from contextlib import contextmanager
from contextvars import ContextVar

import sentry_sdk

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
    if team:
        sentry_sdk.get_current_scope().set_tag("team", team.slug)
    else:
        sentry_sdk.get_current_scope().remove_tag("team")


def unset_current_team():
    _context.set(None)
    sentry_sdk.get_current_scope().remove_tag("team")


@contextmanager
def current_team(team):
    """Context manager used for setting the team outside of requests where the team can be set automatically.
    This is mostly used for auditing but also useful for testing."""
    set_current_team(team)
    try:
        yield
    finally:
        unset_current_team()
