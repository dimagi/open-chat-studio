from django.urls import path

from . import views

app_name = "slack"

urlpatterns = [
    path("install", views.slack_install, name="install"),
]


slack_global_urls = (
    [
        path("slack/events", views.slack_events_handler, name="events"),
        path("slack/oauth_redirect", views.slack_oauth_redirect, name="oauth_redirect"),
    ],
    "slack_global",
)
