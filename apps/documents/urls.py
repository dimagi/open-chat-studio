from django.urls import path

from apps.documents import views

app_name = "documents"

urlpatterns = [
    path("<slug:tab_name>", views.RepositoryHome.as_view(), name="repositories"),
    # List views
    path("files/", views.FileListView.as_view(), name="files_list"),
    path("files/<int:id>/details", views.FileDetails.as_view(), name="file_details"),
    path("files/upload", views.upload_files, name="upload_files"),
    # Detail views
    path("collections/", views.CollectionListView.as_view(), name="collections_list"),
    path("collections/<int:id>/details", views.CollectionDetails.as_view(), name="collection_details"),
]
