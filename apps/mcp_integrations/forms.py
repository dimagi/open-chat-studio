from django import forms

from apps.mcp_integrations.models import McpServer
from apps.service_providers.models import AuthProvider


class McpServerForm(forms.ModelForm):
    auth_provider = forms.ModelChoiceField(
        AuthProvider.objects.none(),
        required=False,
        label="Auth",
        help_text="Select an authentication to use for this action.",
    )

    class Meta:
        model = McpServer
        fields = ["name", "server_url", "transport_type", "auth_provider", "available_tools"]
        help_texts = {"server_url": "The URL of the remote MCP server"}

    def __init__(self, request, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["auth_provider"].queryset = request.team.authprovider_set.all()
        if not self.instance.id:
            # Don't show tools when creating a new MCP server
            del self.fields["available_tools"]
        else:
            self.fields["available_tools"].widget.attrs["readonly"] = True
