from django.urls import path

from apps.documents import views

app_name = "documents"

urlpatterns = [
    path("<slug:tab_name>", views.CollectionsHome.as_view(), name="collections"),
    # Files
    path("files/", views.FileListView.as_view(), name="files_list"),
    path("files/upload", views.upload_files, name="upload_files"),
    path("files/<int:pk>/details", views.FileDetails.as_view(), name="file_details"),
    path("files/<int:pk>/delete", views.delete_file, name="delete_file"),
    path("files/<int:pk>/edit", views.edit_file, name="edit_file"),
    # Collections
    path("collections/", views.CollectionListView.as_view(), name="collections_list"),
    path("collections/new", views.new_collection, name="new_collection"),
    path("collections/<int:pk>/details", views.CollectionDetails.as_view(), name="collection_details"),
    path("collections/<int:pk>/delete", views.delete_collection, name="delete_collection"),
    path("collections/<int:pk>/edit", views.edit_collection, name="edit_collection"),
]
