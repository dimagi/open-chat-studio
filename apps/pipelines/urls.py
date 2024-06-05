from django.urls import path

from apps.generics.urls import make_crud_urls

from . import views

app_name = "pipelines"

urlpatterns = [
    path("data/<int:pk>/", views.pipeline_data, name="pipeline_data"),
]

urlpatterns.extend(make_crud_urls(views, "Pipeline"))
