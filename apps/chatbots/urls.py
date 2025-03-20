from django.urls import path

from . import views

app_name = "chatbots"
urlpatterns = [
    path("", views.chatbots_home, name="chatbots_home"),
    path("table/", views.ChatbotExperimentTableView.as_view(), name="table"),
    path("new/", views.CreateChatbot.as_view(), name="new"),
    path("e/<int:experiment_id>/", views.single_chatbot_home, name="single_chatbot_home"),
    path("e/<int:pk>/edit/", views.EditChatbot.as_view(), name="edit"),
]
