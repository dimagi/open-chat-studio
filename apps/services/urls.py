from django.urls import path

from apps.services import views

app_name = "services"

urlpatterns = [
    path("llm/create/", views.CreateEditLlmProvider.as_view(), name="new_llm"),
    path("llm/<int:pk>/", views.CreateEditLlmProvider.as_view(), name="edit_llm"),
    path("table/<slug:service_type>/", views.ServiceConfigTableView.as_view(), name="table"),
    path("<int:pk>/delete/", views.delete_service_config, name="delete"),
]
