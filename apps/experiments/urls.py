from django.urls import path

from . import views

app_name = "experiments"


def _make_crud_urls(model_name: str, slug: str, prefix: str = None):
    prefix = prefix or slug
    return [
        path(f"{prefix}/", getattr(views, f"{slug}_home"), name=f"{prefix}_home"),
        path(f"{prefix}/new/", getattr(views, f"Create{model_name}").as_view(), name=f"{prefix}_new"),
        path(f"{prefix}/<int:pk>/", getattr(views, f"Edit{model_name}").as_view(), name=f"{prefix}_edit"),
        path(f"{prefix}/<int:pk>/delete/", getattr(views, f"delete_{slug}"), name=f"{prefix}_delete"),
        path(f"{prefix}/table/", getattr(views, f"{model_name}TableView").as_view(), name=f"{prefix}_table"),
    ]


urlpatterns = [
    path("", views.experiments_home, name="experiments_home"),
    # prompts
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
    # experiments
    path("new/", views.CreateExperiment.as_view(), name="new"),
    path("table/", views.ExperimentTableView.as_view(), name="table"),
    path("e/<int:experiment_id>/", views.single_experiment_home, name="single_experiment_home"),
    path("e/<int:pk>/edit/", views.EditExperiment.as_view(), name="edit"),
    path("e/<int:pk>/delete/", views.delete_experiment, name="delete"),
    path("e/<int:experiment_id>/start_session/", views.start_session, name="start_session"),
    path("e/<int:experiment_id>/create_channel/", views.create_channel, name="create_channel"),
    path("e/<int:experiment_id>/update_channel/<int:channel_id>/", views.update_delete_channel, name="update_channel"),
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

urlpatterns.extend(_make_crud_urls("SafetyLayer", "safety_layer", "safety"))
urlpatterns.extend(_make_crud_urls("SourceMaterial", "source_material"))
urlpatterns.extend(_make_crud_urls("Survey", "survey"))
urlpatterns.extend(_make_crud_urls("ConsentForm", "consent_form", "consent"))
urlpatterns.extend(_make_crud_urls("NoActivityMessageConfig", "no_activity_config", "no_activity"))
urlpatterns.extend(_make_crud_urls("Prompt", "prompt"))
