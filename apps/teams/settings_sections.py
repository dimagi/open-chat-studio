"""Team settings page sections."""

from collections.abc import Callable
from dataclasses import dataclass


def _always_visible(request) -> bool:
    return True


def _no_context(request) -> dict:
    return {}


def is_team_admin(request) -> bool:
    return request.team_membership.is_team_admin()


@dataclass(frozen=True)
class SettingsSection:
    key: str
    label: str
    icon: str
    template: str
    #: Key into the page's `stats` dict whose value is shown as a badge next to the nav item.
    count_key: str | None = None
    is_visible: Callable[..., bool] = _always_visible
    get_context: Callable[..., dict] = _no_context
    #: Returns the nav badge count for sections whose count is not in `stats`.
    get_count: Callable[..., int | None] | None = None
