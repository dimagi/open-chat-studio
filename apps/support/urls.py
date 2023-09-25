from django.urls import path

from . import views

app_name = "support"

urlpatterns = [
    path("", views.hijack_user, name="hijack_user"),
]
