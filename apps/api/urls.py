from django.urls import include, path, re_path
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

# The v1 API surface. v1 is frozen against today's URLs and serializers; new endpoints and the
# experiment -> chatbot rename land under v2 (added in a later phase).
v1_patterns = [
    # Keep participants/ lying around for backwards compatability
    path("participants/", views.ParticipantView.as_view(), name="update-participant-data-old"),
    # GET: list participants; POST: update participant data
    path("participants", views.ParticipantView.as_view(), name="participant-data"),
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

# Serve the v1 surface under an optional, non-capturing ``v1/`` prefix. The version is detected
# from the request path by apps.api.versioning.URLPathVersioning, so it is never passed to views.
# Because the prefix is optional and non-capturing:
#   * ``/api/v1/...`` and the unversioned ``/api/...`` alias both resolve to these views, and
#   * reverse() always produces the unversioned URL, keeping existing callers and hyperlinked
#     serializer fields stable.
#
# The v2 surface is mounted first under a capturing ``v2/`` prefix in its own namespace, so
# ``/api/v2/...`` matches there rather than falling through to v1. v2 has no unversioned alias, so
# reverse("api:v2:...") keeps the ``/api/v2/`` prefix.
urlpatterns = [
    path("v2/", include("apps.api.v2.urls")),
    # The team-export surface: a standalone, unversioned API at ``/api/export/`` (not v1/v2). Mounted
    # before the v1 catch-all so ``export/`` isn't swallowed by the optional-``v1/``-prefix match.
    path("export/", include("apps.api.export.urls")),
    re_path(r"^(?:v1/)?", include(v1_patterns)),
]
