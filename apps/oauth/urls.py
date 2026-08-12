from django.urls import include, path, re_path
from oauth2_provider import urls as oauth2_urls
from oauth2_provider import views as oauth2_views

from apps.generics.urls import make_crud_urls
from apps.utils.rate_limit import rate_limited

from . import views

app_name = oauth2_urls.app_name

# custom URL patterns so that the `/.well-known/*` endpoints are at the root
# everything else is at `/o/`
urlpatterns = [
    re_path(
        r"^\.well-known/openid-configuration$",
        oauth2_views.ConnectDiscoveryInfoView.as_view(),
        name="oidc-connect-discovery-info",
    ),
    path(".well-known/jwks.json", oauth2_views.JwksInfoView.as_view(), name="jwks-info"),
    path("o/userinfo/", oauth2_views.UserInfoView.as_view(), name="user-info"),
    # Shadows the upstream `token` route so issuance is counted against the
    # credentials scope. Declared before the include so this pattern matches
    # first; the name and namespace are unchanged, so reverse() is unaffected.
    path("o/token/", rate_limited("credentials")(oauth2_views.TokenView.as_view()), name="token"),
    path("o/", include(oauth2_urls.base_urlpatterns)),
    # Global (team-less) applications are superuser-only and so live outside the team URL space.
    path("o/global-applications/", views.GlobalApplicationHome.as_view(), name="global_application_home"),
    path(
        "o/global-applications/table/",
        views.GlobalApplicationTableView.as_view(),
        name="global_application_table",
    ),
    path("o/global-applications/new/", views.CreateGlobalApplication.as_view(), name="global_application_new"),
    path("o/global-applications/<int:pk>/", views.EditGlobalApplication.as_view(), name="global_application_edit"),
    path(
        "o/global-applications/<int:pk>/delete/",
        views.DeleteGlobalApplication.as_view(),
        name="global_application_delete",
    ),
]

# Team-scoped application management, mounted under `/a/<team_slug>/` by the root URLconf.
team_urlpatterns = (make_crud_urls(views, "Application"), "oauth_apps")
