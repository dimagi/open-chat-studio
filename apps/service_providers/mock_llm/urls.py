from django.urls import path

from . import views

app_name = "mock_llm"

# No trailing slashes: the OpenAI SDK appends the path to the configured base URL as-is.
urlpatterns = [
    path("v1/chat/completions", views.chat_completions, name="chat_completions"),
    path("v1/responses", views.responses, name="responses"),
    path("v1/models", views.models, name="models"),
]
