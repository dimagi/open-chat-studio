from django.urls import path

from apps.files import views
from apps.generics.urls import make_crud_urls

app_name = "files"

urlpatterns = [
    path("<int:pk>/", views.FileView.as_view(), name="base"),
]
urlpatterns.extend(make_crud_urls(views, "File", "file"))
