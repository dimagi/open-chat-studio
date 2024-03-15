from django.urls import path

from apps.annotations import views

app_name = "annotations"

urlpatterns = [
    path("link/", views.LinkTag.as_view(), name="link_tag"),
    path("unlink/", views.UnlinkTag.as_view(), name="unlink_tag"),
]
