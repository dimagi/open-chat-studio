from django.urls import path

from . import views

app_name = "chat"
urlpatterns = [
    path("file/<int:pk>/", views.download_file, name="download_file"),
]
