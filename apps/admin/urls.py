from django.urls import path

from . import views

app_name = "ocs_admin"

urlpatterns = [
    path("", views.admin_home, name="home"),
    path("usage/", views.usage_chart, name="usage_chart"),
    path("export/usage/", views.export_usage, name="export_usage"),
    path("export/whatsapp/", views.export_whatsapp, name="export_whatsapp"),
]
