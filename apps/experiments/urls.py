from django.urls import include, path

from apps.experiments.views.experiment_routes import CreateExperimentRoute, DeleteExperimentRoute, EditExperimentRoute
from apps.generics.urls import make_crud_urls

from . import views

app_name = "experiments"


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
    path("prompt_builder/load_prompts", views.prompt_builder_load_experiments, name="prompt_builder_load_experiments"),
    path(
        "prompt_builder/load_source_material",
        views.prompt_builder_load_source_material,
        name="prompt_builder_load_source_material",
    ),
    # experiments
    path("new/", views.CreateExperiment.as_view(), name="new"),
    path("table/", views.ExperimentTableView.as_view(), {"is_experiment": True}, name="table"),
    path("e/<int:experiment_id>/", views.single_experiment_home, name="single_experiment_home"),
    path("e/<int:experiment_id>/sessions-table/", views.ExperimentSessionsTableView.as_view(), name="sessions-list"),
    path("e/<int:experiment_id>/versions/", views.ExperimentVersionsTableView.as_view(), name="versions-list"),
    path(
        "e/<int:experiment_id>/versions/archive/<int:version_number>/",
        views.archive_experiment_version,
        name="archive-experiment",
    ),
    path(
        "e/<int:experiment_id>/versions/details/<int:version_number>/",
        views.experiment_version_details,
        name="experiment-version-details",
    ),
    path(
        "e/<int:experiment_id>/versions/set_default/<int:version_number>/",
        views.set_default_experiment,
        name="set-default-experiment",
    ),
    path(
        "e/<int:experiment_id>/versions/description/<int:version_number>/update",
        views.update_version_description,
        name="update_version_description",
    ),
    path("e/<int:experiment_id>/versions/create", views.CreateExperimentVersion.as_view(), name="create_version"),
    path("e/<int:experiment_id>/versions/status", views.version_create_status, name="check_version_creation_status"),
    path("e/<int:pk>/edit/", views.EditExperiment.as_view(), name="edit"),
    path("e/<int:pk>/delete/", views.delete_experiment, name="delete"),
    path(
        "e/<int:experiment_id>/v/<int:version_number>/start_authed_web_session/",
        views.start_authed_web_session,
        name="start_authed_web_session",
    ),
    path(
        "e/<int:experiment_id>/v/<int:version_number>/session/<int:session_id>/",
        views.experiment_chat_session,
        name="experiment_chat_session",
    ),
    path(
        "e/<uuid:experiment_id>/v/<int:version_number>/session/<str:session_id>/message/",
        views.experiment_session_message,
        name="experiment_session_message",
    ),
    path(
        "e/<uuid:experiment_id>/v/<int:version_number>/session/<str:session_id>/embed/message/",
        views.experiment_session_message_embed,
        name="experiment_session_message_embed",
    ),
    path(
        "e/<uuid:experiment_id>/session/<str:session_id>/get_response/<slug:task_id>/",
        views.get_message_response,
        name="get_message_response",
    ),
    path(
        "e/<uuid:experiment_id>/session/<str:session_id>/poll_messages/",
        views.poll_messages,
        name="poll_messages",
    ),
    path(
        "e/<uuid:experiment_id>/session/<str:session_id>/poll_messages/embed/",
        views.poll_messages_embed,
        name="poll_messages_embed",
    ),
    # events
    path("e/<int:experiment_id>/events/", include("apps.events.urls")),
    # superuser tools
    path("e/<int:experiment_id>/invitations/", views.experiment_invitations, name="experiment_invitations"),
    path("e/<int:experiment_id>/invitations/send/<str:session_id>/", views.send_invitation, name="send_invitation"),
    path("e/<int:experiment_id>/exports/generate", views.generate_chat_export, name="generate_chat_export"),
    path(
        "e/<int:experiment_id>/exports/result/<slug:task_id>",
        views.get_export_download_link,
        name="get_export_download_link",
    ),
    # public links
    path(
        "e/<uuid:experiment_id>/s/<str:session_id>/",
        views.start_session_from_invite,
        name="start_session_from_invite",
    ),
    path(
        "e/<uuid:experiment_id>/s/<str:session_id>/pre-survey/",
        views.experiment_pre_survey,
        name="experiment_pre_survey",
    ),
    path(
        "e/<uuid:experiment_id>/s/<str:session_id>/chat/",
        views.experiment_chat,
        name="experiment_chat",
    ),
    path(
        "e/<uuid:experiment_id>/s/<str:session_id>/embed/chat/",
        views.experiment_chat_embed,
        name="experiment_chat_embed",
    ),
    path(
        "e/<uuid:experiment_id>/s/<str:session_id>/end/",
        views.end_experiment,
        name="end_experiment",
    ),
    path(
        "e/<uuid:experiment_id>/s/<str:session_id>/review/",
        views.experiment_review,
        name="experiment_review",
    ),
    path(
        "e/<uuid:experiment_id>/s/<str:session_id>/complete/",
        views.experiment_complete,
        name="experiment_complete",
    ),
    path(
        "e/<uuid:experiment_id>/s/<str:session_id>/view/",
        views.experiment_session_details_view,
        name="experiment_session_view",
    ),
    path(
        "e/<uuid:experiment_id>/s/<str:session_id>/paginate/",
        views.experiment_session_pagination_view,
        name="experiment_session_pagination_view",
    ),
    path(
        "e/<uuid:experiment_id>/s/<str:session_id>/messages/",
        views.experiment_session_messages_view,
        name="experiment_session_messages_view",
    ),
    # public link
    path("e/<uuid:experiment_id>/start/", views.start_session_public, name="start_session_public"),
    path("e/<uuid:experiment_id>/embed/start/", views.start_session_public_embed, name="start_session_public_embed"),
    # Experiment Routes
    path(
        "e/<int:experiment_id>/experiment_routes/<str:type>/new",
        CreateExperimentRoute.as_view(),
        name="experiment_route_new",
    ),
    path(
        "e/<int:experiment_id>/experiment_routes/<int:pk>/edit",
        EditExperimentRoute.as_view(),
        name="experiment_route_edit",
    ),
    path(
        "e/<int:experiment_id>/experiment_routes/<int:pk>/delete",
        DeleteExperimentRoute.as_view(),
        name="experiment_route_delete",
    ),
    path("<int:session_id>/file/<int:pk>/", views.download_file, name="download_file"),
    path("<int:session_id>/image/<int:pk>/html/", views.get_image_html, name="get_image_html"),
    path(
        "e/<uuid:experiment_id>/verify_token/<str:token>/",
        views.verify_public_chat_token,
        name="verify_public_chat_token",
    ),
    path(
        "messages/<int:message_id>/rate/<str:rating>/",
        views.rate_message,
        name="rate_message",
    ),
    path(
        "e/<int:experiment_id>/release_status_badge",
        views.get_release_status_badge,
        name="get_release_status_badge",
    ),
    path(
        "e/<int:experiment_id>/migrate/",
        views.migrate_experiment_view,
        name="migrate_experiment",
    ),
    path(
        "experiment/<uuid:experiment_id>/session/<str:session_id>/translate-messages/",
        views.translate_messages_view,
        name="translate_messages",
    ),
]

urlpatterns.extend(make_crud_urls(views, "SafetyLayer", "safety"))
urlpatterns.extend(make_crud_urls(views, "SourceMaterial", "source_material"))
urlpatterns.extend(make_crud_urls(views, "Survey", "survey"))
urlpatterns.extend(make_crud_urls(views, "ConsentForm", "consent"))
