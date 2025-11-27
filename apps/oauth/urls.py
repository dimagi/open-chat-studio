from django.urls import include, path, re_path
from oauth2_provider import urls as oauth2_urls
from oauth2_provider import views as oauth2_views

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
    path("o/", include(oauth2_urls.base_urlpatterns)),
    path("o/", include(oauth2_urls.management_urlpatterns)),
]
