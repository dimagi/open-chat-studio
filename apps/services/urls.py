from django.urls import path

from apps.services import views

app_name = "services"

urlpatterns = [
    path("create/llm/", views.CreateLlmProvider.as_view(), name="new_llm"),
    path("table/<slug:service_type>/", views.ConsentFormTableView.as_view(), name="table"),
    # path("<int:service_id>/", views.manage_service, name="edit"),
    # path("<int:service_id>/delete/", views.delete_service, name="delete"),
]
