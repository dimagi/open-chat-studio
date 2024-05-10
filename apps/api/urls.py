from django.urls import path

from . import views

app_name = "api"

urlpatterns = [
    path("experiments/", views.ExperimentsView.as_view(), name="list-experiments"),
    path("participants/<uuid:participant_id>", views.update_participant_data, name="update-participant-data"),
]
