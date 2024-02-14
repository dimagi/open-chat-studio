from django.urls import path

from apps.files import views

app_name = "files"

urlpatterns = [
    path("<int:pk>/delete/", views.DeleteFile.as_view(), name="delete"),
]
