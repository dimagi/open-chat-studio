from apps.documents import views
from apps.generics.urls import make_crud_urls, path

app_name = "documents"

urlpatterns = [
    path("collections/<int:pk>", views.single_collection_home, name="single_collection_home"),
    path("collections/<int:pk>/add_files", views.add_collection_files, name="add_collection_files"),
]

urlpatterns.extend(make_crud_urls(views, "Collection", "collection"))
