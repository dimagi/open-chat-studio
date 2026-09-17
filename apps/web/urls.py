from django.urls import path, re_path
from django.views.generic import TemplateView

from . import views
from .waf import WafRule, waf_allow

app_name = "web"
urlpatterns = [
    path(
        "robots.txt",
        waf_allow(WafRule.NoUserAgent_HEADER)(
            TemplateView.as_view(template_name="robots.txt", content_type="text/plain")
        ),
        name="robots.txt",
    ),
    path("status/", views.HealthCheck.as_view()),
    path("status/<str:subset>/", views.HealthCheck.as_view()),
    path("sudo/django-admin/", views.elevate_django_admin, name="elevate_django_admin"),
    path("sudo/ocs-admin/", views.elevate_ocs_admin, name="elevate_ocs_admin"),
    path("sudo/team/<slug:team_slug>/", views.elevate_team, name="elevate_team"),
    path("sudo/release/<str:grant>/", views.release_elevation, name="release_elevation"),
    path("search", views.global_search, name="global_search"),
    re_path(r"celery_group_status/(?P<group_id>[\w-]+)/", views.celery_task_group_status, name="celery_group_status"),
]

team_urlpatterns = (
    [
        path("", views.team_home, name="home"),
    ],
    "web_team",
)
