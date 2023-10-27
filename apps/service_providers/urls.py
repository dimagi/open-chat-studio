from django.urls import path

from . import views

app_name = "service_providers"

urlpatterns = [
    path("<slug:provider_type>/table/", views.ServiceProviderTableView.as_view(), name="table"),
    path("<slug:provider_type>/create/", views.CreateServiceProvider.as_view(), name="new"),
    path("<slug:provider_type>/<int:pk>/", views.CreateServiceProvider.as_view(), name="edit"),
    path("<slug:provider_type>/<int:pk>/delete/", views.delete_service_provider, name="delete"),
]
