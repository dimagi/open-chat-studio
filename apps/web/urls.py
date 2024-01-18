from django.urls import path
from django.views.generic import TemplateView

from . import views

app_name = "web"
urlpatterns = [
    path("", views.home, name="home"),
    path("robots.txt", TemplateView.as_view(template_name="robots.txt", content_type="text/plain"), name="robots.txt"),
]

team_urlpatterns = (
    [
        path("", views.team_home, name="home"),
    ],
    "web_team",
)
