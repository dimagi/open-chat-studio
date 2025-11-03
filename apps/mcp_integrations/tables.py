import django_tables2 as tables
from django.conf import settings
from django.urls import reverse

from apps.generics import actions
from apps.mcp_integrations.models import McpServer
from apps.teams.utils import get_slug_for_team


class McpServerTable(tables.Table):
    name = tables.Column(
        linkify=True,
        attrs={
            "a": {"class": "link"},
        },
        orderable=True,
    )
    tool_count = tables.Column(verbose_name="Tools")
    actions = actions.ActionsColumn(
        actions=[
            actions.chip_action(
                label="Refresh",
                url_factory=lambda _, __, record, value: reverse(
                    "mcp_integrations:refresh_tools", args=[get_slug_for_team(record.team_id), record.pk]
                ),
                required_permissions=["mcp_integrations.change_mcpserver"],
                icon_class="fa fa-refresh",
            ),
            actions.edit_action(
                "mcp_integrations:edit",
                required_permissions=["mcp_integrations.change_mcpserver"],
            ),
            actions.delete_action(
                "mcp_integrations:delete",
                required_permissions=["mcp_integrations.delete_mcpserver"],
            ),
        ]
    )

    class Meta:
        model = McpServer
        fields = ("name", "tool_count", "server_url", "transport_type")
        row_attrs = settings.DJANGO_TABLES2_ROW_ATTRS
        orderable = False
        empty_text = "No MCP servers found."
