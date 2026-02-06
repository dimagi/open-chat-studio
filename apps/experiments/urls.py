from django.urls import include, path
from django.views.generic import RedirectView

from apps.experiments.views.experiment_routes import CreateExperimentRoute, DeleteExperimentRoute, EditExperimentRoute
from apps.generics.urls import make_crud_urls

from . import views

app_name = "experiments"


urlpatterns = [
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
    # experiments - redirect to chatbots
    path("new/", RedirectView.as_view(pattern_name="chatbots:new"), name="new"),
    path("e/<int:experiment_id>/trends/data", views.trends_data, name="trends_data"),
    path("e/<int:experiment_id>/versions/", views.ExperimentVersionsTableView.as_view(), name="versions-list"),
    path(
        "e/<int:experiment_id>/versions/archive/<int:version_number>/",
        views.archive_experiment_version,
        name="archive-experiment",
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
    path(
        "e/<int:experiment_id>/versions/create",
        RedirectView.as_view(pattern_name="chatbots:create_version"),
        name="create_version",
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
        "experiment/<uuid:experiment_id>/session/<str:session_id>/translate-messages/",
        views.translate_messages_view,
        name="translate_messages",
    ),
    path(
        "experiment/<int:experiment_id>/versions",
        views.get_experiment_version_names,
        name="get_experiment_version_names",
    ),
]

urlpatterns.extend(make_crud_urls(views, "SourceMaterial", "source_material"))
urlpatterns.extend(make_crud_urls(views, "Survey", "survey"))
urlpatterns.extend(make_crud_urls(views, "ConsentForm", "consent"))
