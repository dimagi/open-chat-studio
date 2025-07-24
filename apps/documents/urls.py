from django.urls import path

from apps.documents import views
from apps.generics.urls import make_crud_urls

app_name = "documents"

urlpatterns = [
    path("collections/<int:pk>", views.single_collection_home, name="single_collection_home"),
    path("collections/<int:pk>/query_view", views.QueryView.as_view(), name="query_collection_view"),
    path("collections/<int:pk>/query", views.query_collection, name="collection_query"),
    path("collections/<int:pk>/add_files", views.add_collection_files, name="add_collection_files"),
    path(
        "collections/<int:pk>/files/<int:file_id>/delete",
        views.delete_collection_file_view,
        name="delete_collection_file",
    ),
    path("collections/<int:collection_id>/files/", views.collection_files_view, name="collection_files_list"),
    path(
        "collections/<int:collection_id>/<int:document_source_id>/files/",
        views.collection_files_view,
        name="document_source_files_list",
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
        "collections/<int:collection_id>/files/<int:pk>/status",
        views.get_collection_file_status,
        name="get_collection_file_status",
    ),
    # document source
    path(
        "collections/<int:collection_id>/source/", views.CreateDocumentSource.as_view(), name="create_document_source"
    ),
    path(
        "collections/<int:collection_id>/source/<int:pk>/",
        views.EditDocumentSource.as_view(),
        name="edit_document_source",
    ),
    path(
        "collections/<int:collection_id>/source/<int:pk>/delete/",
        views.delete_document_source,
        name="delete_document_source",
    ),
    path(
        "collections/<int:collection_id>/source/<int:pk>/sync/",
        views.sync_document_source,
        name="sync_document_source",
    ),
]

urlpatterns.extend(make_crud_urls(views, "Collection", "collection"))
