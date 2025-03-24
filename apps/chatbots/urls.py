from django.urls import include, path

from . import views

app_name = "chatbots"
urlpatterns = [
    path("", views.chatbots_home, name="chatbots_home"),
    path("table/", views.ChatbotExperimentTableView.as_view(), name="table"),
    path("new/", views.CreateChatbot.as_view(), name="new"),
    path("e/<int:experiment_id>/", views.single_chatbot_home, name="single_chatbot_home"),
    path("e/<int:pk>/edit/", views.EditChatbot.as_view(), name="edit"),
    path("e/<int:experiment_id>/versions/create", views.CreateChatbotVersion.as_view(), name="create_version"),
    path("e/<int:experiment_id>/versions/", views.ChatbotVersionsTableView.as_view(), name="versions-list"),
    path(
        "e/<int:experiment_id>/versions/details/<int:version_number>/",
        views.chatbot_version_details,
        name="version-details",
    ),
    path("e/<int:experiment_id>/events/", include("apps.events.urls")),
    path(
        "e/<int:experiment_id>/versions/status",
        views.chatbot_version_create_status,
        name="check_version_creation_status",
    ),
    path("e/<int:experiment_id>/sessions-table/", views.ChatbotSessionsTableView.as_view(), name="sessions-list"),
    path(
        "e/<uuid:experiment_id>/s/<str:session_id>/view/",
        views.chatbot_session_details_view,
        name="chatbot_session_view",
    ),
    path(
        "e/<int:experiment_id>/v/<int:version_number>/session/<int:session_id>/",
        views.chatbot_chat_session,
        name="chatbot_chat_session",
    ),
    path(
        "e/<uuid:experiment_id>/s/<str:session_id>/paginate/",
        views.chatbot_session_pagination_view,
        name="chatbot_session_pagination_view",
    ),
    path(
        "e/<int:experiment_id>/v/<int:version_number>/start_authed_web_session/",
        views.start_authed_web_session,
        name="start_authed_web_session",
    ),
    path(
        "e/<int:experiment_id>/v/<int:version_number>/start_authed_web_session/",
        views.start_authed_web_session,
        name="start_authed_web_session",
    ),
    path("e/<int:experiment_id>/invitations/", views.chatbot_invitations, name="chatbots_invitations"),
]
