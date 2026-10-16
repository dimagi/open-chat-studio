"""Team settings page sections and the registry other apps use to add their own."""

from collections.abc import Callable
from dataclasses import dataclass


def _always_visible(request) -> bool:
    return True


def _no_context(request) -> dict:
    return {}


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


#: Sections added by other apps, keyed by section key, with the key of the section each follows.
_registered: dict[str, tuple[SettingsSection, str]] = {}


def register_settings_section(section: SettingsSection, *, after: str) -> None:
    """Add a section from another app to the settings page, placed after the section keyed `after`."""
    _registered.setdefault(section.key, (section, after))


def with_registered_sections(sections: list[SettingsSection]) -> list[SettingsSection]:
    """Return `sections` with the registered sections inserted in place."""
    result = list(sections)
    for section, after in _registered.values():
        keys = [existing.key for existing in result]
        if after not in keys:
            raise ValueError(f"Team settings section '{section.key}' is placed after '{after}', which does not exist")
        result.insert(keys.index(after) + 1, section)
    return result
