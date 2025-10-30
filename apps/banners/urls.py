from django.urls import path

from . import views

app_name = "banners"

urlpatterns = [
    path("", views.load_banners, name="load_banners"),
]
