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
    path("table/", views.ExperimentTableView.as_view(), name="table"),
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
    path("e/<int:pk>/edit/", views.EditExperiment.as_view(), name="edit"),
    path("e/<int:pk>/delete/", views.delete_experiment, name="delete"),
    path("e/<int:pk>/add_file/", views.AddFileToExperiment.as_view(), name="add_file"),
    path("e/<int:pk>/delete_file/<int:file_id>/", views.DeleteFileFromExperiment.as_view(), name="remove_file"),
    path(
        "e/<int:experiment_id>/v/<int:version_number>/start_authed_web_session/",
        views.start_authed_web_session,
        name="start_authed_web_session",
    ),
    path("e/<int:experiment_id>/create_channel/", views.create_channel, name="create_channel"),
    path("e/<int:experiment_id>/update_channel/<int:channel_id>/", views.update_delete_channel, name="update_channel"),
    path(
        "e/<int:experiment_id>/v/<int:version_number>/session/<int:session_id>/",
        views.experiment_chat_session,
        name="experiment_chat_session",
    ),
    path(
        "e/<str:experiment_id>/v/<int:version_number>/session/<str:session_id>/message/",
        views.experiment_session_message,
        name="experiment_session_message",
    ),
    path(
        "e/<str:experiment_id>/session/<str:session_id>/get_response/<slug:task_id>/",
        views.get_message_response,
        name="get_message_response",
    ),
    path(
        "e/<int:experiment_id>/session/<int:session_id>/poll_messages/",
        views.poll_messages,
        name="poll_messages",
    ),
    # events
    path("e/<int:experiment_id>/events/", include("apps.events.urls")),
    # superuser tools
    path("e/<slug:experiment_id>/invitations/", views.experiment_invitations, name="experiment_invitations"),
    path("e/<slug:experiment_id>/invitations/send/<str:session_id>/", views.send_invitation, name="send_invitation"),
    path("e/<int:experiment_id>/download_chats/", views.download_experiment_chats, name="download_experiment_chats"),
    # public links
    path(
        "e/<slug:experiment_id>/s/<str:session_id>/",
        views.start_session_from_invite,
        name="start_session_from_invite",
    ),
    path(
        "e/<slug:experiment_id>/s/<str:session_id>/pre-survey/",
        views.experiment_pre_survey,
        name="experiment_pre_survey",
    ),
    path(
        "e/<slug:experiment_id>/s/<str:session_id>/chat/",
        views.experiment_chat,
        name="experiment_chat",
    ),
    path(
        "e/<slug:experiment_id>/s/<str:session_id>/end/",
        views.end_experiment,
        name="end_experiment",
    ),
    path(
        "e/<slug:experiment_id>/s/<str:session_id>/review/",
        views.experiment_review,
        name="experiment_review",
    ),
    path(
        "e/<slug:experiment_id>/s/<str:session_id>/complete/",
        views.experiment_complete,
        name="experiment_complete",
    ),
    path(
        "e/<slug:experiment_id>/s/<str:session_id>/view/",
        views.experiment_session_details_view,
        name="experiment_session_view",
    ),
    path(
        "e/<slug:experiment_id>/s/<str:session_id>/paginate/",
        views.experiment_session_pagination_view,
        name="experiment_session_pagination_view",
    ),
    # public link
    path("e/<slug:experiment_id>/start/", views.start_session_public, name="start_session_public"),
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
    path(
        "e/<slug:experiment_id>/verify_token/<str:token>/",
        views.verify_public_chat_token,
        name="verify_public_chat_token",
    ),
]

urlpatterns.extend(make_crud_urls(views, "SafetyLayer", "safety"))
urlpatterns.extend(make_crud_urls(views, "SourceMaterial", "source_material"))
urlpatterns.extend(make_crud_urls(views, "Survey", "survey"))
urlpatterns.extend(make_crud_urls(views, "ConsentForm", "consent"))
