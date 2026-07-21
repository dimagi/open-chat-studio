"""Architecture guard: every view under /a/<team_slug>/ enforces team authorization.

Encodes the rule from ``docs/agents/django_view_security.md`` and
``docs/agents/multi_tenancy.md``: views mounted on the team-scoped URL prefix must
gate access on team membership. The common agent mistake is adding a feature view
under that prefix and forgetting the ``login_and_team_required`` decorator /
``LoginAndTeamRequiredMixin``, silently exposing one team's data to another.

Scope is deliberately limited to the team-scoped URL tree (``team_urlpatterns``).
The public root surface — webhooks, marketing pages, auth, DRF ``/api/`` — is
authorized by other mechanisms (signature checks, DRF global permission defaults)
and is out of scope here.

Only *team-scoped* authorization counts: authentication-only auth (a bare login check)
is rejected, because it confirms the user is logged in but not that they belong to the
team in the URL. There are currently no DRF views under this prefix; if one is added it
will fail this guard until it opts into a team-scoped mechanism (or the allowlist).
"""

import inspect

from django.urls import URLPattern, URLResolver

from apps.teams.decorators import ENFORCES_TEAM_AUTH_ATTR
from config.urls import team_urlpatterns

# Class names (matched across the view's MRO) that gate a CBV on *team-scoped* authorization.
# Deliberately excludes authentication-only mixins (LoginRequiredMixin, UserPassesTestMixin):
# they confirm the user is logged in but not that they belong to the team in the URL, so
# accepting them would let the guard pass tenant-isolation bypasses. PermissionRequiredMixin
# is team-scoped because the custom backend scopes permission checks to the current team
# (see apps/teams/backends.py).
_AUTH_MIXIN_NAMES = frozenset(
    {
        "LoginAndTeamRequiredMixin",
        "PermissionRequiredMixin",
    }
)

# Qualname fragment of Django's team-scoped function-view auth decorator. `functools.wraps`
# hides the decorator identity on the wrapper, but permission_required closes over a nested
# check function whose qualname still identifies it, so we detect it by walking the closure
# chain. login_required / user_passes_test are excluded for the same reason as above: they
# are authentication-only, not team-scoped.
_DJANGO_AUTH_DECORATOR_FRAGMENTS = ("permission_required.<locals>",)

# Team-scoped views that are intentionally NOT team-membership gated. Each entry is a
# view identifier (module.qualname) with a reason. Adding here is an "Ask first" decision
# (see AGENTS.md) — it asserts the view is safe to serve without team-membership auth.
PUBLIC_VIEW_ALLOWLIST: dict[str, str] = {
    # Bare pattern-name redirects issue a 302 to a named URL and touch no data; the redirect
    # target enforces its own authorization (e.g. experiments/new/ -> chatbots:new).
    "django.views.generic.base.RedirectView": "static redirect, no data access",
}


def _iter_view_callbacks(patterns, prefix=""):
    for entry in patterns:
        if isinstance(entry, URLResolver):
            yield from _iter_view_callbacks(entry.url_patterns, prefix + str(entry.pattern))
        elif isinstance(entry, URLPattern):
            yield prefix + str(entry.pattern), entry.callback


def _view_identifier(callback) -> str:
    view_class = getattr(callback, "view_class", None)
    target = view_class or getattr(callback, "cls", None) or callback
    module = getattr(target, "__module__", "?")
    qualname = getattr(target, "__qualname__", getattr(target, "__name__", repr(target)))
    return f"{module}.{qualname}"


def _closure_functions(fn):
    """Yield every function reachable through fn's nested closure cells."""
    seen = set()
    stack = [fn]
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        for cell in getattr(current, "__closure__", None) or ():
            try:
                value = cell.cell_contents
            except ValueError:
                continue
            if inspect.isfunction(value):
                yield value
                stack.append(value)


def _is_team_authorized(callback) -> bool:
    # Class-based views: a team-scoped auth mixin in the MRO.
    view_class = getattr(callback, "view_class", None)
    if view_class is not None and any(klass.__name__ in _AUTH_MIXIN_NAMES for klass in view_class.__mro__):
        return True
    # Function views wrapped by the project's team-auth decorators carry the marker.
    if getattr(callback, ENFORCES_TEAM_AUTH_ATTR, False):
        return True
    # Function views wrapped by Django's permission_required.
    return any(
        fragment in fn.__qualname__
        for fn in _closure_functions(callback)
        for fragment in _DJANGO_AUTH_DECORATOR_FRAGMENTS
    )


def _team_scoped_views():
    seen = {}
    for route, callback in _iter_view_callbacks(team_urlpatterns):
        seen.setdefault(_view_identifier(callback), (route, callback))
    return seen


def test_team_urlpatterns_discoverable():
    assert _team_scoped_views(), "expected to resolve views under the team-scoped URL prefix"


def test_team_scoped_views_enforce_team_auth():
    unauthorized = []
    for identifier, (route, callback) in sorted(_team_scoped_views().items()):
        if identifier in PUBLIC_VIEW_ALLOWLIST:
            continue
        if not _is_team_authorized(callback):
            unauthorized.append(f"  a/<team_slug>/{route}  ->  {identifier}")
    assert not unauthorized, (
        "Views mounted under /a/<team_slug>/ without team authorization "
        "(add login_and_team_required / LoginAndTeamRequiredMixin, or allowlist with a reason "
        "in PUBLIC_VIEW_ALLOWLIST if genuinely public):\n" + "\n".join(unauthorized)
    )


def test_public_view_allowlist_has_no_stale_entries():
    known = set(_team_scoped_views())
    stale = sorted(entry for entry in PUBLIC_VIEW_ALLOWLIST if entry not in known)
    assert not stale, f"PUBLIC_VIEW_ALLOWLIST references views that no longer exist: {stale}"
