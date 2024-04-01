from django.urls import path

from apps.annotations import views
from apps.generics.urls import make_crud_urls

app_name = "annotations"

urlpatterns = [
    path("link/tag/", views.LinkTag.as_view(), name="link_tag"),
    path("unlink/tag", views.UnlinkTag.as_view(), name="unlink_tag"),
    path("link/comment/", views.LinkComment.as_view(), name="link_comment"),
    path("unlink/comment", views.UnlinkComment.as_view(), name="unlink_comment"),
]

urlpatterns.extend(make_crud_urls(views, "Tag", "tag"))
