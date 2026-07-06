from django.urls import include, path
from rest_framework import routers

from apps.api.v2 import views

app_name = "v2"

router = routers.SimpleRouter()
router.register(r"chatbots", views.ChatbotViewSet, basename="chatbot")

# The v2 API surface: the renamed chatbot surface and all new endpoints (e.g. inspect).
# Mounted under the capturing ``v2/`` prefix; unlike v1 there is no unversioned alias.
urlpatterns = [
    path("me/", views.MeView.as_view(), name="me"),
    path("", include(router.urls)),
]
