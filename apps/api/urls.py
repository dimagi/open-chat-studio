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

urlpatterns = [
    path("participants/", views.update_participant_data_old, name="update-participant-data-old"),
    # Duplicate update-participant-data without a trailing "/" for backwards compatibility
    path("participants", views.update_participant_data, name="update-participant-data"),
    path("openai/<uuid:experiment_id>/chat/completions", openai.chat_completions, name="openai-chat-completions"),
    path("files/<int:pk>/content", views.file_content_view, name="file-content"),
    path("commcare_connect/", include((connect_patterns, "commcare-connect"))),
    path("trigger_bot", views.trigger_bot_message, name="trigger_bot"),
    path("sessions/<uuid:id>/end_experiment_session/",views.ExperimentSessionViewSet.as_view({'post': 'end_experiment_session'}),name="end-experiment-session"),
    path("", include(router.urls)),
]
