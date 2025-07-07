from django.urls import path

from . import views

app_name = "teams"

urlpatterns = [
    path("create/", views.create_team, name="create_team"),
    # invitation acceptance views
    path("invitation/<uuid:invitation_id>/", views.accept_invitation, name="accept_invitation"),
]

team_urlpatterns = (
    [
        # team management views
        path("", views.manage_team, name="manage_team"),
        path("delete", views.delete_team, name="delete_team"),
        path("members/<int:membership_id>/", views.team_membership_details, name="team_membership_details"),
        path("members/<int:membership_id>/remove/", views.remove_team_membership, name="remove_team_membership"),
        path("invite/<slug:invitation_id>/", views.resend_invitation, name="resend_invitation"),
        path("invite/", views.send_invitation_view, name="send_invitation"),
        path("invite/cancel/<slug:invitation_id>/", views.cancel_invitation_view, name="cancel_invitation"),
        path("flags/", views.feature_flags, name="feature_flags"),
    ],
    "single_team",
)
