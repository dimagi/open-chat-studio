from ..generics.urls import make_crud_urls
from . import channel_views

app_name = "ocs_notifications_channels"

urlpatterns = make_crud_urls(channel_views, "NotificationChannel")
