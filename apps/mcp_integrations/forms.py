from django import forms

from apps.mcp_integrations.models import McpServer


class McpServerForm(forms.ModelForm):
    class Meta:
        model = McpServer
        fields = ["name", "server_url", "transport_type", "header_name", "header_value"]
        help_texts = {"server_url": "The URL of the remote MCP server"}
