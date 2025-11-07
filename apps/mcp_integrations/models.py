import logging

from asgiref.sync import async_to_sync
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.urls import reverse
from langchain_mcp_adapters.client import MultiServerMCPClient

from apps.teams.models import BaseTeamModel
from apps.teams.utils import get_slug_for_team

logger = logging.getLogger("ocs.mcp_integrations")


class TransportType(models.TextChoices):
    SSE = "sse", "SSE (Server-Sent Events)"
    STREAMABLE_HTTP = "streamable_http", "Streamable HTTP"


class McpServer(BaseTeamModel):
    name = models.CharField(max_length=255)
    server_url = models.URLField()
    transport_type = models.CharField(
        max_length=20, choices=TransportType.choices, default=TransportType.STREAMABLE_HTTP
    )
    auth_provider = models.ForeignKey(
        "service_providers.AuthProvider",
        on_delete=models.SET_NULL,
        related_name="mcp_servers",
        null=True,
        blank=True,
    )
    available_tools = ArrayField(models.CharField(max_length=255), default=list, blank=True)

    class Meta:
        ordering = ("name",)

    @property
    def tool_count(self) -> int:
        return len(self.available_tools)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("mcp_integrations:edit", args=[get_slug_for_team(self.team_id), self.pk])

    def sync_tools(self):
        """
        Fetch tools from the MCP server and update the available_tools field.
        """
        tools = self.fetch_tools()
        self.available_tools = [tool.name[:255] for tool in tools]
        self.save(update_fields=["available_tools"])

    def fetch_tools(self):
        """
        Fetch tools from the MCP server
        """
        headers = {}
        if self.auth_provider:
            auth_service = self.auth_provider.get_auth_service()
            headers |= auth_service.get_auth_headers()

        return self._fetch_tools_from_mcp_server(headers)

    @async_to_sync
    async def _fetch_tools_from_mcp_server(self, headers: dict):
        try:
            client = MultiServerMCPClient(
                {
                    "gateway": {
                        "transport": self.transport_type,
                        "url": self.server_url,
                        "headers": headers,
                    }
                }
            )
            return await client.get_tools()
        except Exception:
            logger.exception(f"Error fetching tools from MCP server {self.name}")
            return []
