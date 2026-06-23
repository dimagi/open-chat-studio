from django.urls import include, path
from rest_framework import routers

from apps.api.v2 import views
from apps.api.v2.cost_tracking import views as cost_tracking_views

app_name = "v2"

router = routers.SimpleRouter()
router.register(r"chatbots", views.ChatbotViewSet, basename="chatbot")

cost_tracking_patterns = (
    [
        path("usage/", cost_tracking_views.CostTrackingUsageView.as_view(), name="usage"),
        path("pricing/", cost_tracking_views.CostTrackingPricingView.as_view(), name="pricing"),
    ],
    "cost_tracking",
)

# The v2 API surface: the renamed chatbot surface and all new endpoints (e.g. inspect).
# Mounted under the capturing ``v2/`` prefix; unlike v1 there is no unversioned alias.
urlpatterns = [
    path("me/", views.MeView.as_view(), name="me"),
    path("cost_tracking/", include(cost_tracking_patterns)),
    path("", include(router.urls)),
]
