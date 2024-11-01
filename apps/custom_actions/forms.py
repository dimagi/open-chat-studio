from django import forms

from apps.custom_actions.fields import JSONOrYAMLField
from apps.custom_actions.models import CustomAction
from apps.service_providers.models import AuthProvider


class CustomActionForm(forms.ModelForm):
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": "3"}), required=False, max_length=1000)
    prompt = forms.CharField(
        widget=forms.Textarea(attrs={"rows": "3"}),
        required=False,
        label="Additional Prompt",
        help_text="Use this field to provide additional instructions to the LLM",
        max_length=1000,
    )
    api_schema = JSONOrYAMLField(
        widget=forms.Textarea(attrs={"rows": "10"}),
        required=True,
        label="API Schema",
        help_text="Paste in the OpenAPI schema for the API you want to interact with. "
        "This will be used to generate the API calls for the LLM. Accepts YAML or JSON.",
        initial={},
    )
    auth_provider = forms.ModelChoiceField(
        AuthProvider.objects.none(),
        required=False,
        label="Auth",
        help_text="Select an authentication to use for this action.",
    )

    class Meta:
        model = CustomAction
        fields = ("name", "description", "auth_provider", "prompt", "api_schema")

    def __init__(self, request, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["auth_provider"].queryset = request.team.authprovider_set.all()

    def clean_api_schema(self):
        api_schema = self.cleaned_data["api_schema"]
        return validate_api_schema(api_schema)


def validate_api_schema(api_schema):
    """Perform very basic validation on the API schema."""

    servers = api_schema.get("servers", [])
    if not servers:
        raise forms.ValidationError("No servers found in the schema.")
    if len(servers) > 1:
        raise forms.ValidationError("Multiple servers found in the schema. Only one is allowed.")
    server = servers[0].get("url")
    if not server or not server.startswith("https"):
        raise forms.ValidationError("Invalid server URL. Must start with 'https'.")

    paths = api_schema.get("paths", {})
    if not paths:
        raise forms.ValidationError("No paths found in the schema.")
    for path, path_items in paths.items():
        if not path.startswith("/"):
            raise forms.ValidationError("Paths must start with a forward slash.")

        for method, operation in path_items.items():
            if method != "get":
                raise forms.ValidationError(f"Invalid method {method} for path {path}. Only 'get' is allowed.")

    return api_schema
