from django.urls import path

from . import views

app_name = "service_providers"

urlpatterns = [
    path("table/", views.LlmProviderTableView.as_view(), name="table"),
    path("create/", views.CreateEditLlmProvider.as_view(), name="new"),
    path("<int:pk>/", views.CreateEditLlmProvider.as_view(), name="edit"),
    path("<int:pk>/delete/", views.delete_service_provider, name="delete"),
]
