from django.conf import settings
from django.urls import path

from . import views

app_name = "prelogin"

# The marketing pages moved to their own site and repo (see the OCS pre-login
# repo's docs/MIGRATION_PLAN.md). The paths stay here as permanent redirects
# rather than 404s: they were indexed for years and are still linked from
# outside, so they keep their equity and their visitors.
_MARKETING = settings.PROJECT_METADATA["MARKETING_SITE_URL"]


def _moved(path_suffix, name):
    return path(
        path_suffix,
        views.PreloginRedirectView.as_view(url=f"{_MARKETING}/{path_suffix}", permanent=True),
        name=name,
    )


urlpatterns = [
    path("", views.home, name="home"),
    _moved("about/", "about"),
    _moved("contact/", "contact"),
    _moved("applications/", "applications"),
    _moved("open-opportunities/", "open_opportunities"),
    # Straight to the marketing home's anchor, not to /platform/ there — that path
    # is itself a 301 on the marketing site, and chaining two redirects costs a
    # round trip and dilutes what crawlers pass along.
    path(
        "platform/",
        views.PreloginRedirectView.as_view(url=f"{_MARKETING}/#how-it-works", permanent=True),
        name="platform",
    ),
]
