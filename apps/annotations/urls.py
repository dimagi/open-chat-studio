from django.urls import path

from apps.annotations import views
from apps.generics.urls import make_crud_urls

app_name = "annotations"

urlpatterns = [
    path("link/tag/", views.LinkTag.as_view(), name="link_tag"),
    path("unlink/tag", views.UnlinkTag.as_view(), name="unlink_tag"),
    path("tag-ui", views.TagUI.as_view(), name="tag_ui"),
    path("add/comment/", views.LinkComment.as_view(), name="add_comment"),
    path("remove/comment", views.UnlinkComment.as_view(), name="remove_comment"),
]

urlpatterns.extend(make_crud_urls(views, "Tag", "tag"))
