from django.urls import path

from apps.files import views

app_name = "files"

urlpatterns = [
    path("<int:pk>/", views.FileView.as_view(), name="base"),
    path("public/<int:pk>/download/", views.download_file, name="public_download"),
]
