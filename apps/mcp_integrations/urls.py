from ..generics.urls import make_crud_urls
from . import views

app_name = "mcp_integrations"

urlpatterns = make_crud_urls(views, "McpServer")
