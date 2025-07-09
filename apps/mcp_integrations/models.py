from django.db import models
from django.urls import reverse

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

    class Meta:
        ordering = ("name",)

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("mcp_integrations:edit", args=[self.team.slug, self.pk])
