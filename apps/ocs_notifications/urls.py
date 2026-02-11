from django.urls import path

from apps.ocs_notifications import views

app_name = "ocs_notifications"

urlpatterns = [
    path("", views.NotificationHome.as_view(), name="notifications_home"),
    path("table/", views.UserNotificationTableView.as_view(), name="notifications_table"),
    path(
        "notification/<int:notification_id>/toggle-read/",
        views.ToggleNotificationReadView.as_view(),
        name="toggle_notification_read",
    ),
    path(
        "notification/<int:notification_id>/mute/",
        views.MuteNotificationView.as_view(),
        name="mute_notification",
    ),
    path(
        "notification/<int:notification_id>/unmute/",
        views.UnmuteNotificationView.as_view(),
        name="unmute_notification",
    ),
    path(
        "toggle-do-not-disturb/",
        views.ToggleDoNotDisturbView.as_view(),
        name="toggle_do_not_disturb",
    ),
]
