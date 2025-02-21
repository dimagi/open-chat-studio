from django.urls import path

from apps.help import views

app_name = "help"

urlpatterns = [
    path("generate_code/", views.pipeline_generate_code, name="pipeline_generate_code"),
]
