import logging
from contextlib import contextmanager
from contextvars import ContextVar, Token
from functools import lru_cache

import sentry_sdk
import structlog
from django.core.cache import cache

log = logging.getLogger("ocs.teams")
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


def set_current_team(team) -> Token:
    """
    Utils to set a team in the current thread/context.
    Used in a middleware once a user is logged in.
    Can be used by doing:
    ```
        get_current_team(team)
    ```
    """
    team = _unwrap_lazy(team)
    # existing_team = get_current_team()
    # Commented out to avoid noise in logs
    # if team is not None and existing_team is not None:
    #     if existing_team != team:
    #         log.error("Trying to set a different team in the current context: %s != %s", team, existing_team)

    token = _context.set(team)
    if team:
        sentry_sdk.get_current_scope().set_tag("team", team.slug)
        structlog.contextvars.bind_contextvars(team=team.slug)
    else:
        sentry_sdk.get_current_scope().remove_tag("team")
        structlog.contextvars.unbind_contextvars("team")
    return token


def unset_current_team(token: Token | None = None):
    """
    When the token that the context was set to is passed, we use that to reset the context to its previous value,
    otherwise we set it to None.
    """
    if token is None:
        _context.set(None)
    else:
        _context.reset(token)
    sentry_sdk.get_current_scope().remove_tag("team")


@contextmanager
def current_team(team):
    """Context manager used for setting the team outside of requests where the team can be set automatically.
    This is mostly used for auditing but also useful for testing."""
    token = set_current_team(team)
    try:
        yield
    finally:
        unset_current_team(token)


def _unwrap_lazy(obj):
    """Unwraps a lazy object if it is one, otherwise returns the object itself."""
    from django.utils.functional import LazyObject, empty

    if isinstance(obj, LazyObject):
        if obj._wrapped is empty:
            obj._setup()
        return obj._wrapped
    return obj


@lru_cache
def get_slug_for_team(team_id: int):
    """Team slugs don't change so it is safe to cache them for a long period of time.
    This function caches them in the Django cache as well as in memory.
    """
    if not team_id or team_id < 0:
        raise ValueError()
    cache_key = f"team_slug:{team_id}"
    slug = cache.get(cache_key)
    if slug is None:
        from apps.teams.models import Team

        slug = Team.objects.values_list("slug", flat=True).get(id=team_id)
        # Cache for 24 hours
        cache.set(cache_key, slug, 24 * 3600)
    return slug
