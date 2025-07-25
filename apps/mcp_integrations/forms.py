from django import forms
from django.conf import settings

from apps.mcp_integrations.models import McpServer
from apps.service_providers.models import AuthProvider
from apps.utils.urlvalidate import InvalidURL, validate_user_input_url


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

    def clean_server_url(self):
        server_url = self.cleaned_data["server_url"]
        try:
            validate_user_input_url(server_url, strict=not settings.DEBUG)
        except InvalidURL as e:
            raise forms.ValidationError(f"The server URL is invalid: {str(e)}") from None

        return server_url
