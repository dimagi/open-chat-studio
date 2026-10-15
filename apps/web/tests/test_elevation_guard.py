"""Architecture guard: every view under /admin/ and /django-admin/ enforces elevation."""

import pytest
from django.contrib import admin
from django.urls import URLPattern, URLResolver, resolve
from django.views.generic import RedirectView

from apps.admin.urls import urlpatterns as ocs_admin_urlpatterns
from apps.web.admin_site import OcsAdminSite
from apps.web.elevation import ENFORCES_ELEVATION_ATTR

# Views on the admin surfaces that are intentionally reachable without elevation, keyed by
# `module.qualname`, with the reason.
UNGATED_VIEW_ALLOWLIST: dict[str, str] = {
    "django.contrib.admin.sites.AdminSite.login": (
        "config/urls.py redirects /django-admin/login/ to the main login page before this pattern"
    ),
}

SURFACES = {
    "admin/": ocs_admin_urlpatterns,
    "django-admin/": admin.site.get_urls(),
}


def _iter_view_callbacks(patterns, prefix=""):
    for entry in patterns:
        if isinstance(entry, URLResolver):
            yield from _iter_view_callbacks(entry.url_patterns, prefix + str(entry.pattern))
        elif isinstance(entry, URLPattern):
            yield prefix + str(entry.pattern), entry.callback


def _view_identifier(callback) -> str:
    target = getattr(callback, "view_class", None) or callback
    target = getattr(target, "__func__", target)
    return f"{target.__module__}.{target.__qualname__}"


def _routes_through_ocs_admin_site(callback) -> bool:
    """Whether `callback` is one of Django's admin URL wrappers, which call `admin_view` per request."""
    # The marker never reaches these wrappers; Django tags them with the site or model admin instead.
    site = getattr(callback, "admin_site", None) or getattr(getattr(callback, "model_admin", None), "admin_site", None)
    return isinstance(site, OcsAdminSite)


def _is_gated(callback) -> bool:
    return getattr(callback, ENFORCES_ELEVATION_ATTR, False) or _routes_through_ocs_admin_site(callback)


def _views():
    return [
        (prefix + route, callback)
        for prefix, patterns in SURFACES.items()
        for route, callback in _iter_view_callbacks(patterns)
    ]


@pytest.mark.parametrize("prefix", list(SURFACES))
def test_surface_is_discoverable(prefix):
    assert list(_iter_view_callbacks(SURFACES[prefix])), f"expected to resolve views under /{prefix}"


def _plain_view(request):
    return None


@pytest.mark.parametrize(
    ("callback", "gated"),
    [
        pytest.param(_plain_view, False, id="bare-view"),
        pytest.param(admin.site.admin_view(_plain_view), True, id="custom-admin-url"),
        pytest.param(admin.site.get_urls()[0].callback, True, id="django-lazy-wrapper"),
    ],
)
def test_guard_tells_gated_views_apart(callback, gated):
    assert _is_gated(callback) is gated


def test_admin_views_enforce_elevation():
    ungated = sorted(
        f"  /{route}  ->  {_view_identifier(callback)}"
        for route, callback in _views()
        if not _is_gated(callback) and _view_identifier(callback) not in UNGATED_VIEW_ALLOWLIST
    )
    assert not ungated, (
        "Admin views reachable without elevation (wrap with requires_elevation, or allowlist "
        "with a reason in UNGATED_VIEW_ALLOWLIST):\n" + "\n".join(ungated)
    )


def test_ungated_view_allowlist_has_no_stale_entries():
    ungated = {_view_identifier(callback) for _, callback in _views() if not _is_gated(callback)}
    stale = sorted(set(UNGATED_VIEW_ALLOWLIST) - ungated)
    assert not stale, f"UNGATED_VIEW_ALLOWLIST references views that are gated or no longer exist: {stale}"


def test_admin_site_login_is_shadowed_by_the_main_login_redirect():
    """The allowlist entry for `AdminSite.login` holds only while this redirect is matched first."""
    assert resolve("/django-admin/login/").func.view_class is RedirectView
