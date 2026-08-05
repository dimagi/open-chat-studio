from celery import shared_task

from apps.mcp_integrations.models import McpServer
from apps.utils.celery import Queues


@shared_task(queue=Queues.BACKGROUND)
def sync_tools_task(mcp_server_id: int):
    """
    Celery task to synchronize tools for an McpServer instance.
    """
    mcp_server = McpServer.objects.get(id=mcp_server_id)
    mcp_server.sync_tools()
