from django.urls import path

from ..generics.urls import make_crud_urls
from . import views

app_name = "custom_actions"

urlpatterns = make_crud_urls(views, "CustomAction")

# Add the health check endpoint
urlpatterns.append(
    path(
        "<int:pk>/check-health/",
        views.CheckCustomActionHealth.as_view(),
        name="check_health",
    )
)

urlpatterns.append(
    path(
        "<int:pk>/test-endpoints/",
        views.CustomActionEndpointTester.as_view(),
        name="test_endpoints",
    )
)

urlpatterns.append(
    path(
        "<int:pk>/test-endpoint/",
        views.TestCustomActionEndpoint.as_view(),
        name="test_endpoint",
    )
)
