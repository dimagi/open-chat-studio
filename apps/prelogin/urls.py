from django.urls import path
from django.views.generic import RedirectView, TemplateView

from . import views

app_name = "prelogin"
urlpatterns = [
    path("", views.home, name="home"),
    path(
        "about/",
        TemplateView.as_view(template_name="prelogin/about.html", extra_context={"active_nav": "about"}),
        name="about",
    ),
    path(
        "contact/",
        TemplateView.as_view(template_name="prelogin/contact.html", extra_context={"active_nav": "contact"}),
        name="contact",
    ),
    path(
        "applications/",
        TemplateView.as_view(template_name="prelogin/applications.html", extra_context={"active_nav": "applications"}),
        name="applications",
    ),
    path(
        "open-opportunities/",
        TemplateView.as_view(template_name="prelogin/open_opportunities.html"),
        name="open_opportunities",
    ),
    path("platform/", RedirectView.as_view(url="/#how-it-works", permanent=True), name="platform"),
]
