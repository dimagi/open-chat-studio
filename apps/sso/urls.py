from django.urls import path

from . import views

app_name = "sso"

urlpatterns = [
    path("accounts/login/", views.CustomLoginView.as_view(), name="login"),
    path(
        "accounts/invitation/<uuid:invitation_id>/signup/",
        views.SignupAfterInvite.as_view(),
        name="signup_after_invite",
    ),
]
