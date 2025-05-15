from django.urls import path

from . import consumers

websocket_urlpatterns = [
    path(
        r"ws/a/<slug:team_slug>/<uuid:chatbot_id>/start/", consumers.BotChatConsumer.as_asgi(), name="ws_bot_chat_start"
    ),
    path(
        r"ws/a/<slug:team_slug>/<uuid:chatbot_id>/<str:session_id>/",
        consumers.BotChatConsumer.as_asgi(),
        name="ws_bot_chat_continue",
    ),
]
