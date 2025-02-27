from django.urls import path

from apps.documents import views

app_name = "documents"

urlpatterns = [
    path("<slug:tab_name>", views.RepositoryHome.as_view(), name="repository_home"),
    # List views
    path("files_list/", views.FileListView.as_view(), name="files_list"),
    path("collections_list/", views.CollectionListView.as_view(), name="collections_list"),
    # Detail views
    path("file_details/<int:id>", views.FileDetails.as_view(), name="file_details"),
    path("collection_details/<int:id>", views.CollectionDetails.as_view(), name="collection_details"),
]
