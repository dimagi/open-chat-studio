from django.urls import path

from apps.annotations import views

app_name = "annotations"

urlpatterns = [
    path("<int:object_id>/link/", views.LinkTag.as_view(), name="link_tag"),
    path("<int:object_id>/unlink/", views.UnlinkTag.as_view(), name="unlink_tag"),
]
