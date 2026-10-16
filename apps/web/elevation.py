import ipaddress
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from functools import wraps
from typing import ClassVar
from urllib.parse import urlencode

from allauth.account.internal import flows
from django.contrib import messages
from django.http import Http404, HttpResponseRedirect
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from apps.teams.models import Team
from apps.utils.rate_limit import forwarded_client_ip
from apps.web.models import SuperuserElevation

logger = logging.getLogger("ocs.audit")

SESSION_KEY = "elevations"

EXPIRY = 60 * 30  # 30 minutes

MAX_CONCURRENT_ELEVATIONS = 5

USER_AGENT_MAX_LENGTH = SuperuserElevation._meta.get_field("user_agent").max_length

REAUTH_CALLBACK = "apps.web.elevation.complete_elevation"

# Marker attribute stamped on views wrapped by `requires_elevation` or `OcsAdminSite.admin_view`.
# `apps/web/tests/test_elevation_guard.py` reads it to confirm every admin view is gated.
# `functools.wraps` hides the decorator identity, so the attribute is the only signal.
ENFORCES_ELEVATION_ATTR = "enforces_elevation"

#: How long a stashed elevation request stays valid. allauth keeps the stash in the session
#: until *some* re-authentication consumes it, so without this an abandoned elevation could be
#: completed minutes or hours later by an unrelated prompt the user answered for another reason.
STASH_MAX_AGE = 120

TOO_MANY_ELEVATIONS_MESSAGE = (
    "You already hold the maximum number of elevated privileges. Release one of them and try again."
)


class TooManyElevations(Exception):
    """Raised when elevating would exceed `MAX_CONCURRENT_ELEVATIONS`."""


class InvalidGrant(ValueError):
    """Raised when a wire form does not name a grant."""


class GrantKind(StrEnum):
    DJANGO_ADMIN = "django_admin"
    OCS_ADMIN = "ocs_admin"
    TEAM = "team"


@dataclass(frozen=True)
class Grant:
    """What a user is elevated into, and who may hold it.

    The wire form is what URLs and the session carry: `django_admin`, `ocs_admin` or
    `team:<slug>`. Namespacing the team grants is what stops a team whose slug happens to
    name an admin surface from unlocking that surface.
    """

    DJANGO_ADMIN: ClassVar["Grant"]
    OCS_ADMIN: ClassVar["Grant"]

    kind: GrantKind
    team_slug: str = ""

    def __post_init__(self):
        # `may_be_held_by` and `label` branch on `kind is GrantKind.TEAM`, so a raw string
        # kind has to become the enum member or a team grant takes the staff branch.
        object.__setattr__(self, "kind", GrantKind(self.kind))

    @classmethod
    def team(cls, slug: str) -> "Grant":
        if not slug:
            raise InvalidGrant("A team grant needs a slug")
        return cls(GrantKind.TEAM, slug)

    @classmethod
    def parse(cls, wire_form: str) -> "Grant":
        kind, _, team_slug = str(wire_form).partition(":")
        try:
            kind = GrantKind(kind)
        except ValueError:
            raise InvalidGrant(f"Unknown grant: {wire_form!r}") from None

        if kind is GrantKind.TEAM:
            return cls.team(team_slug)
        if team_slug:
            raise InvalidGrant(f"Unknown grant: {wire_form!r}")
        return cls(kind)

    def __str__(self) -> str:
        return f"{self.kind}:{self.team_slug}" if self.team_slug else str(self.kind)

    @property
    def label(self) -> str:
        if self.kind is GrantKind.TEAM:
            return f'Team "{self.team_slug}"'
        return _LABELS[self.kind]

    def may_be_held_by(self, user) -> bool:
        """Team elevation stands in for membership of that team, so it stays superuser-only."""
        if self.kind is GrantKind.TEAM:
            return user.is_superuser
        return user.is_staff

    def acquire_url(self) -> str:
        if self.kind is GrantKind.TEAM:
            return reverse("web:elevate_team", args=[self.team_slug])
        return reverse(_ACQUIRE_URL_NAMES[self.kind])


Grant.DJANGO_ADMIN = Grant(GrantKind.DJANGO_ADMIN)
Grant.OCS_ADMIN = Grant(GrantKind.OCS_ADMIN)

_LABELS = {
    GrantKind.DJANGO_ADMIN: "Django admin",
    GrantKind.OCS_ADMIN: "OCS admin",
}

_ACQUIRE_URL_NAMES = {
    GrantKind.DJANGO_ADMIN: "web:elevate_django_admin",
    GrantKind.OCS_ADMIN: "web:elevate_ocs_admin",
}


@dataclass(frozen=True)
class ActiveElevation:
    grant: Grant
    expires_at: datetime
    label: str


@dataclass(frozen=True)
class PendingElevation:
    grant: Grant
    label: str


class Elevation:
    """The grants held by one request's session, keyed by wire form."""

    def __init__(self, request):
        self.request = request

    def has(self, grant: Grant) -> bool:
        return str(grant) in self._held()

    def is_full(self) -> bool:
        return len(self._held()) >= MAX_CONCURRENT_ELEVATIONS

    def add(self, grant: Grant) -> int | None:
        """Hold `grant`, returning its expiry, or None when it was already held."""
        held = self._held()
        if str(grant) in held:
            return None

        if len(held) >= MAX_CONCURRENT_ELEVATIONS:
            logger.warning(
                "elevation.denied",
                extra={"user_id": self.request.user.pk, "grant": str(grant), "reason": "too_many"},
            )
            raise TooManyElevations(
                f"Cannot grant '{grant}': maximum of {MAX_CONCURRENT_ELEVATIONS} concurrent elevations already held"
            )

        expires = _now() + EXPIRY
        self._store(held | {str(grant): expires})
        return expires

    def drop(self, grant: Grant) -> int | None:
        """Stop holding `grant`, returning the expiry it had, or None when it was not held."""
        held = self._held()
        if str(grant) not in held:
            return None

        self._store({wire_form: expires for wire_form, expires in held.items() if wire_form != str(grant)})
        return held[str(grant)]

    def active(self) -> dict[str, datetime]:
        """The expiry of each held grant, keyed by wire form."""
        return {wire_form: datetime.fromtimestamp(expires) for wire_form, expires in self._held().items()}

    def _held(self) -> dict[str, int]:
        """The unexpired entries, pruning the session of anything else."""
        stored = self.request.session.get(SESSION_KEY) or {}
        now = _now()
        retained = {wire_form: expires for wire_form, expires in stored.items() if expires > now and _parses(wire_form)}
        if len(retained) != len(stored):
            logger.info(
                "elevation.expired",
                extra={"user_id": self.request.user.pk, "grants": sorted(set(stored) - set(retained))},
            )
            self._store(retained)
        return retained

    def _store(self, held: dict[str, int]) -> None:
        # Only ever called with contents that differ from what is stored: this runs from
        # `project_meta` on every rendered page, and assigning marks the session modified.
        self.request.session[SESSION_KEY] = held


def elevate(request, grant: Grant) -> None:
    """Grant `grant` to the request's session and record it. Raises `TooManyElevations` at the cap."""
    expires = Elevation(request).add(grant)
    if expires is None:
        return

    SuperuserElevation.objects.create(
        user=request.user,
        grant=str(grant),
        granted_at=timezone.now(),
        expires_at=_as_datetime(expires),
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent", "")[:USER_AGENT_MAX_LENGTH],
    )
    logger.info("elevation.granted", extra={"user_id": request.user.pk, "grant": str(grant), "expires_at": expires})


def release(request, grant: Grant) -> bool:
    """Release `grant` before it expires, returning whether it was held."""
    expires = Elevation(request).drop(grant)
    if expires is None:
        return False

    # The session keeps only the expiry, so that is what identifies the row.
    SuperuserElevation.objects.filter(
        user=request.user, grant=str(grant), expires_at=_as_datetime(expires), released_at__isnull=True
    ).update(released_at=timezone.now())
    logger.info("elevation.released", extra={"user_id": request.user.pk, "grant": str(grant)})
    return True


def _as_datetime(timestamp: int) -> datetime:
    return datetime.fromtimestamp(timestamp, tz=UTC)


def _client_ip(request) -> str | None:
    try:
        return str(ipaddress.ip_address(forwarded_client_ip(request)))
    except ValueError:
        return None


def active_elevations(request) -> dict[str, ActiveElevation]:
    if not hasattr(request, "user") or request.user.is_anonymous:
        return {}
    active = Elevation(request).active()
    grants = {wire_form: Grant.parse(wire_form) for wire_form in active}
    labels = grant_labels(grants.values())
    return {wire_form: ActiveElevation(grant, active[wire_form], labels[grant]) for wire_form, grant in grants.items()}


def grant_labels(grants: Iterable[Grant]) -> dict[Grant, str]:
    """Labels for `grants`, naming each team alongside its slug, which is what tells same-named teams apart."""
    grants = list(grants)
    slugs = {grant.team_slug for grant in grants if grant.kind is GrantKind.TEAM}
    names = dict(Team.objects.filter(slug__in=slugs).values_list("slug", "name")) if slugs else {}
    return {
        grant: f'Team "{names[grant.team_slug]}" ({grant.team_slug})' if grant.team_slug in names else grant.label
        for grant in grants
    }


def _parses(wire_form: str) -> bool:
    try:
        Grant.parse(wire_form)
    except InvalidGrant:
        return False
    return True


def _now() -> int:
    return int(timezone.now().timestamp())


def _is_stale(state: dict) -> bool:
    return _now() - state.get("at", 0) > STASH_MAX_AGE


def safe_redirect_url(url: str) -> str:
    """Where to send the user once they are done here, falling back to the site root."""
    if not url or not url_has_allowed_host_and_scheme(url, allowed_hosts=None):
        return "/"
    return url


def requires_elevation(grant, superuser_only: bool = False):
    """Gate a view on holding `grant`, sending anyone who does not hold it off to acquire it.

    `grant` is a `Grant`, or a callable taking the view's own arguments and returning one for
    grants that depend on the URL. `superuser_only` raises the bar above the grant's minimum
    role, for admin views that were superuser-only before elevation covered them.
    """

    def decorator(view_func):
        @wraps(view_func)
        def _inner(request, *args, **kwargs):
            resolved = grant(request, *args, **kwargs) if callable(grant) else grant
            redirect = elevation_redirect(request, resolved, superuser_only=superuser_only)
            return redirect or view_func(request, *args, **kwargs)

        setattr(_inner, ENFORCES_ELEVATION_ATTR, True)
        return _inner

    return decorator


def elevation_redirect(request, grant: Grant, superuser_only: bool = False) -> HttpResponseRedirect | None:
    """Where to send a request that does not hold `grant`, or None when it does.

    Everyone lacking the role gets the same `Http404`, so the elevated surfaces are not
    discoverable by probing for a login redirect.
    """
    user = request.user
    if not grant.may_be_held_by(user):
        raise Http404
    if superuser_only and not user.is_superuser:
        raise Http404

    if Elevation(request).has(grant):
        return None

    return acquire_redirect(request, grant)


def acquire_redirect(request, grant: Grant) -> HttpResponseRedirect:
    """Send the request off to acquire `grant`, returning to the current page once it is held."""
    next_url = safe_redirect_url(request.get_full_path())
    return HttpResponseRedirect(f"{grant.acquire_url()}?{urlencode({'next': next_url})}")


def start_elevation(request, grant: Grant, next_url: str):
    """Hand the identity proof to allauth, resuming at `complete_elevation`.

    `stash_and_reauthenticate` always prompts, offers password or MFA, is rate limited, and
    raises `PermissionDenied` when the user has neither. The `did_recently_authenticate` timer
    is deliberately not used: it is global, five minutes wide, and returns True unconditionally
    for users with no usable password and no MFA.
    """
    state = {"grant": str(grant), "next": next_url, "at": _now()}
    return flows.reauthentication.stash_and_reauthenticate(request, state, REAUTH_CALLBACK)


def pending_elevation(request) -> PendingElevation | None:
    """The grant waiting on an identity proof, so the re-authentication prompt can name it."""
    if not hasattr(request, "session"):
        return None

    stash = request.session.get(flows.reauthentication.STATE_SESSION_KEY) or {}
    if stash.get("callback") != REAUTH_CALLBACK:
        return None
    state = stash.get("state") or {}
    try:
        grant = Grant.parse(state["grant"])
    except (InvalidGrant, KeyError, TypeError):
        return None

    if _is_stale(state):
        return None
    return PendingElevation(grant, grant_labels([grant])[grant])


def complete_elevation(request, state: dict):
    """Grant the stashed elevation now that allauth has proved the user's identity."""
    try:
        grant = Grant.parse(state.get("grant", ""))
    except InvalidGrant:
        logger.warning(
            "elevation.denied",
            extra={"user_id": request.user.pk, "grant": state.get("grant"), "reason": "unknown_grant"},
        )
        return HttpResponseRedirect("/")

    if _is_stale(state):
        logger.warning("elevation.denied", extra={"user_id": request.user.pk, "grant": str(grant), "reason": "stale"})
        messages.error(request, "That request for elevated access expired. Please try again.")
        return HttpResponseRedirect("/")

    if not grant.may_be_held_by(request.user):
        logger.warning("elevation.denied", extra={"user_id": request.user.pk, "grant": str(grant), "reason": "role"})
        messages.error(request, "You are not allowed to hold that elevated access.")
        return HttpResponseRedirect("/")

    try:
        elevate(request, grant)
    except TooManyElevations:
        messages.error(request, TOO_MANY_ELEVATIONS_MESSAGE)
        return HttpResponseRedirect("/")

    return HttpResponseRedirect(safe_redirect_url(state.get("next", "")))
