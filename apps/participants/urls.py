from django.urls import path

from apps.generics.urls import make_crud_urls
from apps.participants import views

app_name = "participants"

urlpatterns = [
    path(
        "<int:participant_id>",
        views.SingleParticipantHome.as_view(),
        name="single-participant-home",
    ),
    path(
        "<int:participant_id>/e/<int:experiment_id>",
        views.SingleParticipantHome.as_view(),
        name="single-participant-home-with-experiment",
    ),
    path(
        "<int:participant_id>/data/<int:experiment_id>/update",
        views.EditParticipantData.as_view(),
        name="edit-participant-data",
    ),
    path("participants/<int:pk>/edit_name/", views.edit_name, name="edit_name"),
    path(
        "participants/<int:participant_id>/cancel_schedule/<str:schedule_id>/",
        views.cancel_schedule,
        name="cancel_schedule",
    ),
    path(
        "participants/identifiers/<int:experiment_id>/",
        views.participant_identifiers_by_experiment,
        name="participant_identifier",
    ),
    path("participants/identifiers/", views.all_participant_identifiers, name="all_participant_identifiers"),
    path("participants/import/", views.import_participants, name="import"),
    path("participants/export/", views.export_participants, name="export"),
    path("participants/<int:pk>/delete/", views.DeleteParticipant.as_view(), name="participant_delete"),
    path("<int:participant_id>/trigger_bot/", views.trigger_bot, name="trigger_bot"),
]

urlpatterns.extend(make_crud_urls(views, "Participant", "participant", edit=False, delete=False, new=False))
