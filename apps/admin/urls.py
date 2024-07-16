from django.urls import path

from . import views

app_name = "ocs_admin"

urlpatterns = [
    path("", views.admin_home, name="home"),
    path("usage/", views.usage_chart, name="usage_chart"),
]
