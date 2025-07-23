from django.conf import settings
from django_tables2 import tables

from apps.generics import actions
from apps.mcp_integrations.models import McpServer


class McpServerTable(tables.Table):
    name = tables.columns.Column(
        linkify=True,
        attrs={
            "a": {"class": "link"},
        },
        orderable=True,
    )
    tool_count = tables.columns.Column(verbose_name="Tools")
    actions = actions.ActionsColumn(
        actions=[
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
