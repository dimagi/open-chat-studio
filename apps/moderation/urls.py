from django.urls import path

from apps.moderation import views

_MODERATION_PROVIDER = {"provider_type": "moderation"}

app_name = "moderation"

urlpatterns = [
    path("settings/", views.moderation_settings, name="settings"),
    path("blocked/table/", views.DeniedParticipantTableView.as_view(), name="denied_participants_table"),
    path(
        "provider/create/<str:subtype>/",
        views.ModerationProviderDialog.as_view(),
        _MODERATION_PROVIDER,
        name="provider_new",
    ),
    path(
        "provider/<int:pk>/",
        views.ModerationProviderDialog.as_view(),
        _MODERATION_PROVIDER,
        name="provider_edit",
    ),
]
