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
]

urlpatterns.extend(make_crud_urls(views, "Collection", "collection"))
