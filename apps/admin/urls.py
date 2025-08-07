from django.urls import path

from . import views

app_name = "ocs_admin"

urlpatterns = [
    path("", views.admin_home, name="home"),
    path("configuration/", views.configuration, name="configuration"),
    path("usage/", views.usage_chart, name="usage_chart"),
    path("export/usage/", views.export_usage, name="export_usage"),
    path("export/whatsapp/", views.export_whatsapp, name="export_whatsapp"),
    # Feature Flags
    path("flags/", views.flags_home, name="flags_home"),
    path("flags/<str:flag_name>/", views.flag_detail, name="flag_detail"),
    path("flags/<str:flag_name>/update/", views.update_flag, name="update_flag"),
    path("flags/<str:flag_name>/history/", views.flag_history, name="flag_history"),
    # API endpoints for TomSelect
    path("api/teams/", views.teams_api, name="teams_api"),
    path("api/users/", views.users_api, name="users_api"),
]
