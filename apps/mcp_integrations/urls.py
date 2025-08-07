from django.urls import path

from ..generics.urls import make_crud_urls
from . import views

app_name = "mcp_integrations"

urlpatterns = [
    path("<int:pk>/refresh_tools", views.trigger_refresh_view, name="refresh_tools"),
] + make_crud_urls(views, "McpServer")
