from django.urls import path

from . import views

app_name = "ocs_admin"

urlpatterns = [
    path("", views.admin_home, name="home"),
    path("usage/", views.usage_chart, name="usage_chart"),
    path("export/usage/", views.export_usage, name="export_usage"),
    path("export/whatsapp/", views.export_whatsapp, name="export_whatsapp"),
    # Feature Flags
    path("flags/", views.flags_home, name="flags_home"),
    path("flags/<int:flag_id>/", views.flag_detail, name="flag_detail"),
    path("flags/<int:flag_id>/update/", views.update_flag, name="update_flag"),
]
