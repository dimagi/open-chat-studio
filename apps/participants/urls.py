from django.urls import path

from apps.generics.urls import make_crud_urls
from apps.participants import views

app_name = "participants"

urlpatterns = [
    path("<int:participant_id>/", views.SingleParticipantHome.as_view(), name="single-participant-home"),
    path(
        "<int:participant_id>/data/<int:experiment_id>/update",
        views.EditParticipantData.as_view(),
        name="edit-participant-data",
    ),
    path(
        "<int:participant_id>/experiments/<int:experiment_id>", views.ExperimentData.as_view(), name="experiment_data"
    ),
    path("participants/<int:pk>/edit_name/", views.edit_name, name="edit_name"),
    path("participants/<int:pk>/update_name/", views.update_name, name="update_name"),
]

urlpatterns.extend(make_crud_urls(views, "Participant", "participant", edit=False, delete=False, new=False))
