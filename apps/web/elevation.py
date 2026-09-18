import logging
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import ClassVar

from allauth.account.internal import flows
from django.contrib import messages
from django.http import HttpResponseRedirect
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

logger = logging.getLogger("ocs.audit")

SESSION_KEY = "elevations"

EXPIRY = 60 * 30  # 30 minutes

MAX_CONCURRENT_ELEVATIONS = 5

REAUTH_CALLBACK = "apps.web.elevation.complete_elevation"

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


Grant.DJANGO_ADMIN = Grant(GrantKind.DJANGO_ADMIN)
Grant.OCS_ADMIN = Grant(GrantKind.OCS_ADMIN)

_LABELS = {
    GrantKind.DJANGO_ADMIN: "Django admin",
    GrantKind.OCS_ADMIN: "OCS admin",
}


@dataclass(frozen=True)
class ActiveElevation:
    grant: Grant
    expires_at: datetime


class Elevation:
    """The grants held by one request's session, keyed by wire form."""

    def __init__(self, request):
        self.request = request

    def has(self, grant: Grant) -> bool:
        return str(grant) in self._held()

    def is_full(self) -> bool:
        return len(self._held()) >= MAX_CONCURRENT_ELEVATIONS

    def add(self, grant: Grant) -> None:
        held = self._held()
        if str(grant) in held:
            return

        if len(held) >= MAX_CONCURRENT_ELEVATIONS:
            logger.warning(
                f"Denied elevation of '{self.request.user.email}' to '{grant}': "
                f"already holding {len(held)} (max {MAX_CONCURRENT_ELEVATIONS})"
            )
            raise TooManyElevations(
                f"Cannot grant '{grant}': maximum of {MAX_CONCURRENT_ELEVATIONS} concurrent elevations already held"
            )

        logger.info(f"Elevating '{self.request.user.email}' to '{grant}'")
        self._store(held | {str(grant): _now() + EXPIRY})

    def drop(self, grant: Grant) -> bool:
        """Release `grant`, returning whether it was held."""
        held = self._held()
        if str(grant) not in held:
            return False

        logger.info(f"Releasing elevation of '{self.request.user.email}' to '{grant}'")
        self._store({wire_form: expires for wire_form, expires in held.items() if wire_form != str(grant)})
        return True

    def active(self) -> dict[str, ActiveElevation]:
        return {
            wire_form: ActiveElevation(Grant.parse(wire_form), datetime.fromtimestamp(expires))
            for wire_form, expires in self._held().items()
        }

    def _held(self) -> dict[str, int]:
        """The unexpired entries, pruning the session of anything else."""
        stored = self.request.session.get(SESSION_KEY) or {}
        now = _now()
        retained = {wire_form: expires for wire_form, expires in stored.items() if expires > now and _parses(wire_form)}
        if len(retained) != len(stored):
            dropped = sorted(set(stored) - set(retained))
            logger.info(f"Dropped elevations for '{self.request.user.email}': {','.join(dropped)}")
            self._store(retained)
        return retained

    def _store(self, held: dict[str, int]) -> None:
        # Only ever called with contents that differ from what is stored: this runs from
        # `project_meta` on every rendered page, and assigning marks the session modified.
        self.request.session[SESSION_KEY] = held


def active_elevations(request) -> dict[str, ActiveElevation]:
    if not hasattr(request, "user") or request.user.is_anonymous:
        return {}
    return Elevation(request).active()


def _parses(wire_form: str) -> bool:
    try:
        Grant.parse(wire_form)
    except InvalidGrant:
        return False
    return True


def _now() -> int:
    return int(timezone.now().timestamp())


def safe_redirect_url(url: str) -> str:
    """Where to send the user once they are done here, falling back to the site root."""
    if not url or not url_has_allowed_host_and_scheme(url, allowed_hosts=None):
        return "/"
    return url


def start_elevation(request, grant: Grant, next_url: str):
    """Hand the identity proof to allauth, resuming at `complete_elevation`.

    `stash_and_reauthenticate` always prompts, offers password or MFA, is rate limited, and
    raises `PermissionDenied` when the user has neither. The `did_recently_authenticate` timer
    is deliberately not used: it is global, five minutes wide, and returns True unconditionally
    for users with no usable password and no MFA.
    """
    state = {"grant": str(grant), "next": next_url, "at": _now()}
    return flows.reauthentication.stash_and_reauthenticate(request, state, REAUTH_CALLBACK)


def pending_elevation(request) -> Grant | None:
    """The grant waiting on an identity proof, so the re-authentication prompt can name it."""
    if not hasattr(request, "session"):
        return None

    stash = request.session.get(flows.reauthentication.STATE_SESSION_KEY) or {}
    if stash.get("callback") != REAUTH_CALLBACK:
        return None
    try:
        return Grant.parse(stash["state"]["grant"])
    except (InvalidGrant, KeyError, TypeError):
        return None


def complete_elevation(request, state: dict):
    """Grant the stashed elevation now that allauth has proved the user's identity."""
    try:
        grant = Grant.parse(state.get("grant", ""))
    except InvalidGrant:
        logger.warning(f"Discarding elevation of '{request.user.email}': unknown grant {state.get('grant')!r}")
        return HttpResponseRedirect("/")

    if _now() - state.get("at", 0) > STASH_MAX_AGE:
        logger.warning(f"Discarding stale elevation request of '{request.user.email}' to '{grant}'")
        messages.error(request, "That request for elevated access expired. Please try again.")
        return HttpResponseRedirect("/")

    if not grant.may_be_held_by(request.user):
        logger.warning(f"Denied elevation of '{request.user.email}' to '{grant}': insufficient role")
        messages.error(request, "You are not allowed to hold that elevated access.")
        return HttpResponseRedirect("/")

    try:
        Elevation(request).add(grant)
    except TooManyElevations:
        messages.error(request, TOO_MANY_ELEVATIONS_MESSAGE)
        return HttpResponseRedirect("/")

    return HttpResponseRedirect(safe_redirect_url(state.get("next", "")))
