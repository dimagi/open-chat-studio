"""GPT Playground URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/3.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.templatetags.static import static as static_url
from django.urls import include, path
from django.views.generic import RedirectView, TemplateView
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView

from apps.oauth.views import TeamScopedAuthorizationView
from apps.slack.urls import slack_global_urls
from apps.teams.urls import team_urlpatterns as single_team_urls
from apps.web.sitemaps import StaticViewSitemap
from apps.web.urls import team_urlpatterns as web_team_urls
from apps.web.waf import WafRule, waf_allow

sitemaps = {
    "static": StaticViewSitemap(),
}

# urls that are unique to using a team should go here
team_urlpatterns = [
    path("", include(web_team_urls)),
    path("team/", include(single_team_urls)),
    path("experiments/", include("apps.experiments.urls")),
    path("service_providers/", include("apps.service_providers.urls")),
    path("assistants/", include("apps.assistants.urls")),
    path("actions/", include("apps.custom_actions.urls")),
    path("pipelines/", include("apps.pipelines.urls")),
    path("files/", include("apps.files.urls")),
    path("annotations/", include("apps.annotations.urls")),
    path("participants/", include("apps.participants.urls")),
    path("mcp_integrations/", include("apps.mcp_integrations.urls")),
    path("slack/", include("apps.slack.urls")),
    path("help/", include("apps.help.urls")),
    path("documents/", include("apps.documents.urls")),
    path("chatbots/", include("apps.chatbots.urls")),
    path("dashboard/", include("apps.dashboard.urls", namespace="dashboard")),
    path("analysis/", include("apps.analysis.urls", namespace="analysis")),
    path("evaluations/concordance/", include("apps.assessments.urls")),
    path("evaluations/", include("apps.evaluations.urls")),
    path("traces/", include("apps.trace.urls")),
    path("filters/", include("apps.filters.urls")),
    path("notifications/", include("apps.ocs_notifications.urls")),
    path("human-annotations/", include("apps.human_annotations.urls")),
]
urlpatterns = [
    path("favicon.ico", RedirectView.as_view(url=static_url("images/favicons/favicon-96x96.png"), permanent=True)),
    path("admin/", include("apps.admin.urls")),
    # redirect Django admin login to main login page
    path("django-admin/login/", RedirectView.as_view(pattern_name=settings.LOGIN_URL)),
    path("django-admin/", admin.site.urls),
    path("o/authorize/", TeamScopedAuthorizationView.as_view(), name="oauth_authorize"),
    path("", include("apps.oauth.urls")),
    path("i18n/", include("django.conf.urls.i18n")),
    path(
        "sitemap.xml",
        waf_allow(WafRule.NoUserAgent_HEADER)(sitemap),
        {"sitemaps": sitemaps},
        name="django.contrib.sitemaps.views.sitemap",
    ),
    path("a/<slug:team_slug>/", include(team_urlpatterns)),
    path("", include("apps.sso.urls")),  # must be before allauth urls since it uses the same paths
    path("accounts/", include("allauth.urls")),  # MFA URLs included automatically
    path("users/", include("apps.users.urls")),
    path("teams/", include("apps.teams.urls")),
    path("", include("apps.prelogin.urls")),
    path("", include("apps.web.urls")),
    path("", include(slack_global_urls)),
    path("celery-progress/", include("celery_progress.urls")),
    path("banners/", include("apps.banners.urls")),
    # API docs. The version is read from the request path by apps.api.versioning.URLPathVersioning,
    # so each schema view emits only its version's surface. The unversioned ``/api/schema/`` is the
    # permanent v1 alias; ``/api/v2/...`` serves the renamed v2 surface (ADR-0022). The export surface
    # is standalone (not v1/v2): its views report an ``"export"`` pseudo-version, so the export schema
    # is served with ``api_version="export"`` to isolate it. ``/api/docs/`` is a landing page linking
    # to the per-surface Redoc references and the guides site. These must precede the ``api/`` include
    # below so the schema/docs paths aren't routed into the API itself.
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/v2/schema/", SpectacularAPIView.as_view(), name="schema-v2"),
    path(
        "api/export/schema/",
        SpectacularAPIView.as_view(
            api_version="export",
            custom_settings={
                "DESCRIPTION": (
                    "Read-only, team-scoped endpoints for the Open Chat Studio data sync/export. "
                    "**Unversioned and intended only for OCS export** — it carries no "
                    "backwards-compatibility guarantee and may change without notice."
                ),
            },
        ),
        name="schema-export",
    ),
    path("api/docs/", TemplateView.as_view(template_name="api/docs_landing.html"), name="api-docs"),
    path("api/v1/docs/", SpectacularRedocView.as_view(url_name="schema"), name="redoc-v1"),
    path("api/v2/docs/", SpectacularRedocView.as_view(url_name="schema-v2"), name="redoc-v2"),
    path("api/export/docs/", SpectacularRedocView.as_view(url_name="schema-export"), name="redoc-export"),
    path("channels/", include("apps.channels.urls", namespace="channels")),
    path("anymail/", include("anymail.urls")),
    path("api/", include("apps.api.urls", namespace="api")),
    path("tz_detect/", include("tz_detect.urls")),
    path("__reload__/", include("django_browser_reload.urls")),
    path("silk/", include("silk.urls", namespace="silk")),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

if settings.USE_DEBUG_TOOLBAR:
    from debug_toolbar.toolbar import debug_toolbar_urls

    urlpatterns.extend(debug_toolbar_urls())
