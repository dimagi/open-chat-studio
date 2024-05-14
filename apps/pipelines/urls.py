from django.urls import path

from . import views

app_name = "pipelines"

urlpatterns = [
    path("", views.pipeline_builder, name="pipeline_builder"),
    path("<int:pk>/", views.get_pipeline, name="get_pipeline"),
    path("input_types/", views.pipeline_node_input_types, name="pipeline_node_input_types"),
]
