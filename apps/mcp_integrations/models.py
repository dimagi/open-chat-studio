from asgiref.sync import async_to_sync
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.urls import reverse
from langchain_mcp_adapters.client import MultiServerMCPClient

from apps.teams.models import BaseTeamModel


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
        return reverse("mcp_integrations:edit", args=[self.team.slug, self.pk])

    def sync_tools(self):
        """
        Fetch tools from the MCP server and update the available_tools field.
        This method should be called after creating or updating the MCP server.
        """
        tools = self.fetch_tools()
        self.available_tools = [tool.name for tool in tools]
        self.save(update_fields=["available_tools"])

    @async_to_sync
    async def fetch_tools(self):
        headers = {}
        if self.auth_provider:
            auth_client = self.auth_provider.get_auth_service().get_http_client()
            headers[auth_client.auth.key] = auth_client.auth.value

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
