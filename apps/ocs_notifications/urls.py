from django.urls import path

from apps.ocs_notifications import views

app_name = "ocs_notifications"

urlpatterns = [
    path("", views.NotificationHome.as_view(), name="notifications_home"),
    path("table/", views.NotificationReceiptTableView.as_view(), name="notifications_table"),
]
