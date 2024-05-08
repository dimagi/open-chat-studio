from django.urls import path

from . import views

app_name = "api"

urlpatterns = [
    path("experiments/", views.get_experiments, name="list-experiments"),
]
