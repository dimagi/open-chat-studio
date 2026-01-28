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
    path("preferences/", views.notification_preferences, name="notification_preferences"),
]
