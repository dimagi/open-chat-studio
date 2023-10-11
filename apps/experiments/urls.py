from django.urls import path

from . import views

app_name = "experiments"

urlpatterns = [
    path("", views.experiments_home, name="experiments_home"),
    path("safety/", views.safety_layer_home, name="safety_home"),
    path("safety/new/", views.CreateSafetyLayer.as_view(), name="safety_new"),
    path("safety/table/", views.SafetyLayerTableView.as_view(), name="safety_table"),
    path("prompt_builder", views.experiments_prompt_builder, name="experiments_prompt_builder"),
    path(
        "prompt_builder/get_message/",
        views.experiments_prompt_builder_get_message,
        name="experiments_prompt_builder_get_message",
    ),
    path(
        "prompt_builder/get_response/",
        views.get_prompt_builder_message_response,
        name="get_prompt_builder_message_response",
    ),
    path(
        "prompt_builder/get_history/",
        views.get_prompt_builder_history,
        name="get_prompt_builder_history",
    ),
    path(
        "prompt_builder/prompt_builder_start_save_process/",
        views.prompt_builder_start_save_process,
        name="prompt_builder_start_save_process",
    ),
    path("prompt_builder/load_prompts", views.prompt_builder_load_prompts, name="prompt_builder_load_prompts"),
    path(
        "prompt_builder/load_source_material",
        views.prompt_builder_load_source_material,
        name="prompt_builder_load_source_material",
    ),
    path("e/<int:experiment_id>/", views.single_experiment_home, name="single_experiment_home"),
    path("e/<int:experiment_id>/start_session/", views.start_session, name="start_session"),
    path(
        "e/<int:experiment_id>/session/<int:session_id>/", views.experiment_chat_session, name="experiment_chat_session"
    ),
    path(
        "e/<int:experiment_id>/session/<int:session_id>/message/",
        views.experiment_session_message,
        name="experiment_session_message",
    ),
    path(
        "e/<int:experiment_id>/session/<int:session_id>/get_response/<slug:task_id>/",
        views.get_message_response,
        name="get_message_response",
    ),
    path(
        "e/<int:experiment_id>/session/<int:session_id>/poll_messages/",
        views.poll_messages,
        name="poll_messages",
    ),
    # superuser tools
    path("e/<slug:experiment_id>/invitations/", views.experiment_invitations, name="experiment_invitations"),
    path("e/<slug:experiment_id>/invitations/send/<slug:session_id>/", views.send_invitation, name="send_invitation"),
    path("e/<int:experiment_id>/download_chats/", views.download_experiment_chats, name="download_experiment_chats"),
    # public links
    path(
        "e/<slug:experiment_id>/s/<slug:session_id>/",
        views.start_experiment_session,
        name="start_experiment_session",
    ),
    path(
        "e/<slug:experiment_id>/s/<slug:session_id>/pre-survey/",
        views.experiment_pre_survey,
        name="experiment_pre_survey",
    ),
    path(
        "e/<slug:experiment_id>/s/<slug:session_id>/chat/",
        views.experiment_chat,
        name="experiment_chat",
    ),
    path(
        "e/<slug:experiment_id>/s/<slug:session_id>/end/",
        views.end_experiment,
        name="end_experiment",
    ),
    path(
        "e/<slug:experiment_id>/s/<slug:session_id>/review/",
        views.experiment_review,
        name="experiment_review",
    ),
    path(
        "e/<slug:experiment_id>/s/<slug:session_id>/complete/",
        views.experiment_complete,
        name="experiment_complete",
    ),
    path(
        "e/<slug:experiment_id>/s/<slug:session_id>/view/",
        views.experiment_session_view,
        name="experiment_session_view",
    ),
    # public link
    path("e/<slug:experiment_id>/start/", views.start_experiment, name="start_experiment"),
]
