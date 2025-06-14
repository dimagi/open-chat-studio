from django.urls import path

from . import views

app_name = "events"


urlpatterns = [
    path("static/new/", views.create_static_event_view, name="static_event_new"),
    path("timeout/new/", views.create_timeout_event_view, name="timeout_event_new"),
    path("static/<int:trigger_id>", views.edit_static_event_view, name="static_event_edit"),
    path("timeout/<int:trigger_id>", views.edit_timeout_event_view, name="timeout_event_edit"),
    path("static/<int:trigger_id>/delete", views.delete_static_event_view, name="static_event_delete"),
    path("timeout/<int:trigger_id>/delete", views.delete_timeout_event_view, name="timeout_event_delete"),
    path("static/<int:trigger_id>/logs", views.static_logs_view, name="static_logs_view"),
    path("timeout/<int:trigger_id>/logs", views.timeout_logs_view, name="timeout_logs_view"),
    path("static/<int:trigger_id>/toggle-active", views.toggle_static_active_status, name="static_event_toggle"),
    path("timeout/<int:trigger_id>/toggle-active", views.toggle_timeout_active_status, name="timeout_event_toggle"),
]
