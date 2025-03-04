from django.urls import path

from . import views

app_name = "sso"

urlpatterns = [
    path("accounts/login/", views.CustomLoginView.as_view(), name="login"),
]
