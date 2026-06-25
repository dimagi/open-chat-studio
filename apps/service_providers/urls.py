from django.urls import path

from . import views

app_name = "service_providers"

urlpatterns = [
    path("llm_provider_model/create/", views.create_llm_provider_model, name="llm_provider_model_new"),
    path(
        "llm_provider_model/<int:pk>/delete/",
        views.delete_llm_provider_model,
        name="llm_provider_model_delete",
    ),
    path(
        "llm_provider_model/<int:pk>/pricing/override/",
        views.PricingOverrideView.as_view(),
        name="pricing_override_form",
    ),
    path(
        "llm_provider_model/<int:pk>/pricing/override/submit/",
        views.PricingOverrideView.as_view(),
        name="pricing_override",
    ),
    path(
        "llm_provider_model/<int:pk>/pricing/revert/",
        views.pricing_revert,
        name="pricing_revert",
    ),
    path("<slug:provider_type>/table/", views.ServiceProviderTableView.as_view(), name="table"),
    path("<slug:provider_type>/create/<str:subtype>/", views.CreateServiceProvider.as_view(), name="new"),
    path("<slug:provider_type>/<int:pk>/", views.CreateServiceProvider.as_view(), name="edit"),
    path("<slug:provider_type>/<int:pk>/usages/", views.ServiceProviderUsagesView.as_view(), name="usages"),
    path("<slug:provider_type>/<int:pk>/delete/", views.delete_service_provider, name="delete"),
    path("<slug:provider_type>/<int:pk>/remove-file/<int:file_id>", views.remove_file, name="delete_file"),
    path("<slug:provider_type>/<int:pk>/upload-file/", views.AddFileToProvider.as_view(), name="add_file"),
    path("<slug:provider_type>/<int:pk>/sync-voices/", views.sync_voices, name="sync_voices"),
]
