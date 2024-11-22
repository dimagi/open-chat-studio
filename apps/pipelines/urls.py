from django.urls import path

from apps.generics.urls import make_crud_urls

from . import views

app_name = "pipelines"

urlpatterns = [
    path("data/<int:pk>/", views.pipeline_data, name="pipeline_data"),
    path("<int:pk>/details", views.pipeline_details, name="details"),
    path("<int:pk>/runs_table/", views.PipelineRunsTableView.as_view(), name="runs_table"),
    path("<int:pipeline_pk>/run/<int:run_pk>", views.run_details, name="run_details"),
    path("<int:pipeline_pk>/message/", views.simple_pipeline_message, name="pipeline_message"),
    path(
        "<int:pipeline_pk>/message/get_response/<slug:task_id>",
        views.get_pipeline_message_response,
        name="pipeline_message_response",
    ),
]

urlpatterns.extend(make_crud_urls(views, "Pipeline"))
