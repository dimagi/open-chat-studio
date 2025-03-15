from django.urls import path

from . import views

app_name = "chatbots"
urlpatterns = [
    path("", views.chatbots_home, name="chatbots_home"),
    path("table/", views.ChatbotTableView.as_view(), name="table"),
    path("new/", views.CreateChatbot.as_view(), name="new"),
]
