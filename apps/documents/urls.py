from django.urls import path

from apps.documents import views

app_name = "documents"

urlpatterns = [
    path("", views.RepositoryHome.as_view(), name="repository_home"),
    path("files_list/", views.FileListView.as_view(), name="files_list"),
    path("file_details/<int:id>", views.FileDetails.as_view(), name="file_details"),
]
