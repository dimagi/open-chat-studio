from django.urls import include, path

from . import views

app_name = "chatbots"
urlpatterns = [
    path("", views.chatbots_home, name="chatbots_home"),
    path("table/", views.ChatbotExperimentTableView.as_view(), name="table"),
    path("new/", views.CreateChatbot.as_view(), {"new_chatbot": True}, name="new"),
    path("<int:experiment_id>/", views.single_chatbot_home, name="single_chatbot_home"),
    path("<int:pk>/edit/", views.EditChatbot.as_view(), name="edit"),
    path("<int:pk>/delete/", views.archive_chatbot, name="archive"),
    path("<int:experiment_id>/versions/create", views.CreateChatbotVersion.as_view(), name="create_version"),
    path("<int:experiment_id>/versions/", views.ChatbotVersionsTableView.as_view(), name="versions-list"),
    path(
        "<int:experiment_id>/versions/details/<int:version_number>/",
        views.chatbot_version_details,
        name="version-details",
    ),
    path("<int:experiment_id>/events/", include("apps.events.urls")),
    path(
        "<int:experiment_id>/versions/status",
        views.chatbot_version_create_status,
        name="check_version_creation_status",
    ),
    path("<int:experiment_id>/sessions-table/", views.ChatbotSessionsTableView.as_view(), name="sessions-list"),
    path(
        "e/<uuid:experiment_id>/s/<str:session_id>/view/",
        views.chatbot_session_details_view,
        name="chatbot_session_view",
    ),
    path(
        "<int:experiment_id>/v/<int:version_number>/session/<int:session_id>/",
        views.chatbot_chat_session,
        name="chatbot_chat_session",
    ),
    path(
        "<uuid:experiment_id>/s/<str:session_id>/paginate/",
        views.chatbot_session_pagination_view,
        name="chatbot_session_pagination_view",
    ),
    path(
        "<int:experiment_id>/v/<int:version_number>/start_authed_web_session/",
        views.start_authed_web_session,
        name="start_authed_web_session",
    ),
    path(
        "<int:experiment_id>/v/<int:version_number>/start_authed_web_session/",
        views.start_authed_web_session,
        name="start_authed_web_session",
    ),
    path("<int:experiment_id>/invitations/", views.chatbot_invitations, name="chatbots_invitations"),
    path("<uuid:experiment_id>/start/", views.start_chatbot_session_public, name="start_session_public"),
    path(
        "<uuid:experiment_id>/s/<str:session_id>/chat/",
        views.chatbot_chat,
        name="chatbot_chat",
    ),
    path(
        "<uuid:experiment_id>/embed/start/",
        views.start_chatbot_session_public_embed,
        name="start_session_public_embed",
    ),
    path(
        "<uuid:experiment_id>/s/<str:session_id>/embed/chat/",
        views.chatbot_chat_embed,
        name="chatbot_chat_embed",
    ),
    path(
        "<int:experiment_id>/settings/cancel-edit/",
        views.cancel_edit_mode,
        name="cancel_edit_mode",
    ),
    path(
        "<int:experiment_id>/settings/save-all/",
        views.chatbots_settings,
        name="settings",
    ),
    path("<int:pk>/copy/", views.copy_chatbot, name="copy"),
    path("sessions/", views.AllSessionsHome.as_view(), name="all_sessions_home"),
    path("sessions-list/", views.ChatbotSessionsTableView.as_view(), name="all_sessions_list"),
]
