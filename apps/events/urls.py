from django.urls import path

from . import views

app_name = "events"


urlpatterns = [
    path("static/new/", views.create_static_event_view, name="static_event_new"),
    path("timeout/new/", views.create_timeout_event_view, name="timeout_event_new"),
    path("static/edit/<int:trigger_id>", views.edit_static_event_view, name="static_event_edit"),
    path("timeout/edit/<int:trigger_id>", views.edit_timeout_event_view, name="timeout_event_edit"),
]
