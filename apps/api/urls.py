from django.urls import include, path
from rest_framework import routers

from . import openai, views

app_name = "api"

router = routers.SimpleRouter()
router.register(r"experiments", views.ExperimentViewSet, basename="experiment")
router.register(r"sessions", views.ExperimentSessionViewSet, basename="session")

connect_patterns = [
    path("generate_key", views.generate_key, name="generate_key"),
    path("callback", views.callback, name="callback"),
    path("consent", views.consent, name="consent"),
]

chat_patterns = [
    path("start/", views.chat_start_session, name="start-session"),
    path("<uuid:session_id>/upload/", views.chat_upload_file, name="upload-file"),
    path("<uuid:session_id>/message/", views.chat_send_message, name="send-message"),
    path("<uuid:session_id>/poll/", views.chat_poll_response, name="poll-response"),
    path("<uuid:session_id>/<str:task_id>/poll/", views.chat_poll_task_response, name="task-poll-response"),
]

urlpatterns = [
    path("participants/", views.UpdateParticipantDataOldView.as_view(), name="update-participant-data-old"),
    # Duplicate update-participant-data without a trailing "/" for backwards compatibility
    path("participants", views.UpdateParticipantDataView.as_view(), name="update-participant-data"),
    path(
        "openai/<uuid:experiment_id>/chat/completions",
        openai.ChatCompletionsView.as_view(),
        name="openai-chat-completions",
    ),
    path(
        "openai/<uuid:experiment_id>/v<int:version>/chat/completions",
        openai.ChatCompletionsVersionView.as_view(),
        name="openai-chat-completions-versioned",
    ),
    path("files/<int:pk>/content", views.FileContentView.as_view(), name="file-content"),
    path("commcare_connect/", include((connect_patterns, "commcare-connect"))),
    path("trigger_bot", views.TriggerBotMessageView.as_view(), name="trigger_bot"),
    path("chat/", include((chat_patterns, "chat"))),
    path("", include(router.urls)),
]
