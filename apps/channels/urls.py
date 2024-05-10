from django.urls import path

from . import views

app_name = "channels"

urlpatterns = [
    path("telegram/<uuid:channel_external_id>", views.new_telegram_message, name="new_telegram_message"),
    # `new_twilio_whatsapp_message` is a legacy route. Use `new_twilio_message` for all twilio messages instead
    path("whatsapp/incoming_message", views.new_twilio_message, name="new_twilio_whatsapp_message"),
    path("twilio/incoming_message", views.new_twilio_message, name="new_twilio_message"),
    path("sureadhere/<str:client_id>/incoming_message", views.new_sureadhere_message, name="new_sureadhere_message"),
    path("whatsapp/turn/<uuid:experiment_id>/incoming_message", views.new_turn_message, name="new_turn_message"),
    path("api/<uuid:experiment_id>/incoming_message", views.new_api_message, name="new_api_message"),
]
