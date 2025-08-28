from django.urls import path

from . import views

app_name = "channels"

urlpatterns = [
    path("telegram/<uuid:channel_external_id>", views.new_telegram_message, name="new_telegram_message"),
    # `new_twilio_whatsapp_message` is a legacy route. Use `new_twilio_message` for all twilio messages instead
    path("whatsapp/incoming_message", views.new_twilio_message, name="new_twilio_whatsapp_message"),
    path("twilio/incoming_message", views.new_twilio_message, name="new_twilio_message"),
    path(
        "sureadhere/<str:sureadhere_tenant_id>/incoming_message",
        views.new_sureadhere_message,
        name="new_sureadhere_message",
    ),
    path("whatsapp/turn/<uuid:experiment_id>/incoming_message", views.new_turn_message, name="new_turn_message"),
    path("api/<uuid:experiment_id>/incoming_message", views.new_api_message, name="new_api_message"),
    path(
        "api/<uuid:experiment_id>/v<int:version>/incoming_message",
        views.new_api_message_versioned,
        name="new_api_message_versioned",
    ),
    path("commcare_connect/incoming_message", views.new_connect_message, name="new_connect_message"),
    path(
        "<slug:team_slug>/chatbots/<int:experiment_id>/channels/<int:channel_id>/edit-dialog/",
        views.channel_edit_dialog,
        name="channel_edit_dialog",
    ),
    path(
        "<slug:team_slug>/chatbots/<int:experiment_id>/channels/create-dialog/<str:platform_value>/",
        views.channel_create_dialog,
        name="channel_create_dialog",
    ),
]
