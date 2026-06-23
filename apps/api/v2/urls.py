from django.urls import include, path, re_path
from rest_framework import routers

from apps.api.general import views as sync_views
from apps.api.v2 import views
from apps.teams.sync.manifest import MANIFEST_ENTRIES

app_name = "v2"

router = routers.SimpleRouter()
router.register(r"chatbots", views.ChatbotViewSet, basename="chatbot")

resource_patterns = [
    path(
        f"{entry.resource}/",
        sync_views.resource_view(entry).as_view(),
        {"resource": entry.resource},
        name=f"resource-{entry.resource}",
    )
    for entry in MANIFEST_ENTRIES
]

# The v2 API surface: the renamed chatbot surface and all new endpoints (e.g. inspect).
# Mounted under the capturing ``v2/`` prefix; unlike v1 there is no unversioned alias.
urlpatterns = [
    path("me/", views.MeView.as_view(), name="me"),
    path("manifest/", sync_views.ManifestView.as_view(), name="manifest"),
    path("", include(router.urls)),
    *resource_patterns,
    re_path(r"^(?P<resource>[a-z_]+)/$", sync_views.UnknownResourceView.as_view(), name="resource"),
]
