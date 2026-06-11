"""Single source of truth for chat widget version policy.

Update LATEST_VERSION in the same PR that publishes a new widget release.
Add a WidgetDeprecation entry to deprecate old versions. See
docs/developer_guides/widget_versioning.md for the full process.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import wraps

from django.conf import settings
from django.utils import timezone
from django.utils.http import http_date
from packaging.version import InvalidVersion, Version

LATEST_VERSION = "0.9.0"

# Widgets older than 0.5.1 (Sept 2025) do not send the x-ocs-widget-version
# header, so a missing/unparseable version is treated as older than everything.
MAX_VERSION_LENGTH = 32


def widget_docs_url() -> str:
    """The published chat widget docs URL, from settings."""
    return f"{settings.DOCUMENTATION_BASE_URL}{settings.DOCUMENTATION_LINKS['chat_widget']}"


@dataclass(frozen=True)
class WidgetDeprecation:
    below_version: str  # all versions < this are deprecated
    sunset_at: datetime  # tz-aware; RFC 8594 semantics — intent, not enforcement
    docs_url: str = field(default_factory=widget_docs_url)


DEPRECATIONS: list[WidgetDeprecation] = [
    WidgetDeprecation(below_version="0.6.0", sunset_at=datetime(2026, 10, 1, tzinfo=UTC)),
]


@dataclass(frozen=True)
class WidgetUpdateStatus:
    level: str  # DaisyUI badge/alert level: "info" or "warning"
    icon: str  # Font Awesome icon class, e.g. "fa-triangle-exclamation"
    message: str
    deprecation: WidgetDeprecation | None = None


def widget_script_url() -> str:
    return (
        f"https://unpkg.com/open-chat-studio-widget@{LATEST_VERSION}"
        "/dist/open-chat-studio-widget/open-chat-studio-widget.esm.js"
    )


def clean_widget_version(raw: str | None) -> str | None:
    """Return the version string if it is a sane version, else None."""
    if not raw or len(raw) > MAX_VERSION_LENGTH:
        return None
    if _parse(raw) is None:
        return None
    return raw


def latest_deprecation() -> WidgetDeprecation | None:
    """The most recent deprecation — the one with the highest `below_version`."""
    if not DEPRECATIONS:
        return None
    return max(DEPRECATIONS, key=lambda d: Version(d.below_version))


def is_deprecated(version: str | None, deprecation: WidgetDeprecation) -> bool:
    """Whether `version` falls under `deprecation`.

    A missing or unparseable version is treated as older than everything.
    """
    parsed = _parse(version)
    return parsed is None or parsed < Version(deprecation.below_version)


def get_deprecation(version: str | None) -> WidgetDeprecation | None:
    """The deprecation covering `version`, or None if it is still supported.

    Only the most recent deprecation is considered, so deprecated widgets are
    always told about the highest-version sunset rather than an older one.
    """
    deprecation = latest_deprecation()
    if deprecation and is_deprecated(version, deprecation):
        return deprecation
    return None


def is_outdated(version: str | None) -> bool:
    """True if `version` is a known version older than LATEST_VERSION."""
    parsed = _parse(version)
    return parsed is not None and parsed < Version(LATEST_VERSION)


def get_widget_update_status(version: str | None) -> WidgetUpdateStatus | None:
    """Status for UI badges. None means no badge (current, or never reported)."""
    if version is None:
        return None
    if deprecation := get_deprecation(version):
        if timezone.now() >= deprecation.sunset_at:
            return WidgetUpdateStatus(
                level="error",
                icon="fa-circle-xmark",
                message=f"Widget version {version} is unsupported — sunset {deprecation.sunset_at:%d %b %Y}.",
                deprecation=deprecation,
            )
        return WidgetUpdateStatus(
            level="warning",
            icon="fa-triangle-exclamation",
            message=(f"Widget version {version} is deprecated — support ends {deprecation.sunset_at:%d %b %Y}."),
            deprecation=deprecation,
        )
    if is_outdated(version):
        return WidgetUpdateStatus(
            level="info",
            icon="fa-circle-arrow-up",
            message=f"Widget {version} in use — {LATEST_VERSION} available.",
        )
    return None


WIDGET_VERSION_HEADER = "x-ocs-widget-version"


def apply_widget_sunset_headers(request, response):
    """Add RFC 8594 headers when the calling widget's version is deprecated.

    Only applies when the widget version header is present: requests without
    it (API users, authenticated sessions) are not widget traffic.
    """
    raw = request.headers.get(WIDGET_VERSION_HEADER)
    if raw is None:
        return response
    deprecation = get_deprecation(clean_widget_version(raw))
    if deprecation:
        response.headers["Deprecation"] = "true"
        response.headers["Sunset"] = http_date(deprecation.sunset_at.timestamp())
        response.headers["Link"] = f'<{deprecation.docs_url}>; rel="successor-version"'
    return response


def widget_sunset_headers(view_func):
    """View decorator form of apply_widget_sunset_headers."""

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        response = view_func(request, *args, **kwargs)
        return apply_widget_sunset_headers(request, response)

    return wrapper


def _parse(version: str | None) -> Version | None:
    if not version:
        return None
    try:
        return Version(version)
    except InvalidVersion:
        return None
