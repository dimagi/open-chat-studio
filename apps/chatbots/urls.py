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
]
