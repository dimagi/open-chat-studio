from django.urls import path

from apps.documents import views
from apps.generics.urls import make_crud_urls

app_name = "documents"

urlpatterns = [
    path("collections/<int:pk>", views.single_collection_home, name="single_collection_home"),
    path("collections/<int:pk>/add_files", views.add_collection_files, name="add_collection_files"),
    path(
        "collections/<int:pk>/files/<int:file_id>/delete", views.delete_collection_file, name="delete_collection_file"
    ),
    path(
        "collections/<int:collection_id>/files/<int:file_id>/chunks",
        views.FileChunkEmbeddingListView.as_view(),
        name="file_chunks",
    ),
    path("collections/<int:pk>/retry_failed_uploads", views.retry_failed_uploads, name="retry_failed_uploads"),
    path(
        "collections/create-from-assistant", views.CreateCollectionFromAssistant.as_view(), name="create_from_assistant"
    ),
    path(
        "collections/<int:pk>/files/<int:file_id>/status",
        views.get_collection_file_status,
        name="get_collection_file_status",
    ),
]

urlpatterns.extend(make_crud_urls(views, "Collection", "collection"))
