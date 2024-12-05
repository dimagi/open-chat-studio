from urllib.parse import urljoin

from django import forms
from django.conf import settings
from django.core.validators import URLValidator
from langchain_community.utilities.openapi import OpenAPISpec

from apps.custom_actions.fields import JsonOrYamlField
from apps.custom_actions.models import CustomAction
from apps.custom_actions.schema_utils import get_operations_from_spec
from apps.service_providers.models import AuthProvider
from apps.utils.urlvalidate import InvalidURL, validate_user_input_url


class CustomActionForm(forms.ModelForm):
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": "3"}), required=False, max_length=1000)
    prompt = forms.CharField(
        widget=forms.Textarea(attrs={"rows": "3"}),
        required=False,
        label="Additional Prompt",
        help_text="Use this field to provide additional instructions to the LLM",
        max_length=1000,
    )
    api_schema = JsonOrYamlField(
        widget=forms.Textarea(attrs={"rows": "10"}),
        required=True,
        label="API Schema",
        help_text="Paste in the OpenAPI schema for the API you want to interact with. "
        "The API must use HTTPS. This will be used to generate the API calls for the LLM. "
        "Accepts YAML or JSON.",
        initial={},
    )
    auth_provider = forms.ModelChoiceField(
        AuthProvider.objects.none(),
        required=False,
        label="Auth",
        help_text="Select an authentication to use for this action.",
    )
    allowed_operations = forms.MultipleChoiceField(widget=forms.CheckboxSelectMultiple, required=False)

    class Meta:
        model = CustomAction
        fields = ("name", "description", "auth_provider", "prompt", "api_schema", "allowed_operations")

    def __init__(self, request, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["auth_provider"].queryset = request.team.authprovider_set.all()
        if not self.instance or not self.instance.id:
            del self.fields["allowed_operations"]
        else:
            grouped_ops = {}
            for op in self.instance.operations:
                grouped_ops.setdefault(op.path, []).append(op)
            self.fields["allowed_operations"].choices = [
                (path, [(op.operation_id, str(op)) for op in ops]) for path, ops in grouped_ops.items()
            ]

    def clean_api_schema(self):
        api_schema = self.cleaned_data["api_schema"]
        return validate_api_schema(api_schema)

    def clean(self):
        if self.errors:
            return

        from apps.chat.agent.openapi_tool import openapi_spec_op_to_function_def

        schema = self.cleaned_data.get("api_schema")
        operations = self.cleaned_data.get("allowed_operations")
        if schema is None or operations is None:
            return self.cleaned_data

        spec = OpenAPISpec.from_spec_dict(schema)
        operations_by_id = {op.operation_id: op for op in get_operations_from_spec(spec)}
        invalid_operations = set(operations) - set(operations_by_id)
        if invalid_operations:
            raise forms.ValidationError(
                {"allowed_operations": f"Invalid operations selected: {', '.join(sorted(invalid_operations))}"}
            )

        for op_id in operations:
            op = operations_by_id[op_id]
            try:
                openapi_spec_op_to_function_def(spec, op.path, op.method)
            except ValueError as e:
                raise forms.ValidationError({"allowed_operations": f"The '{op}' operation is not supported ({e})"})

        return {**self.cleaned_data, "allowed_operations": operations}


def validate_api_schema(api_schema):
    """Perform very basic validation on the API schema."""

    try:
        OpenAPISpec.from_spec_dict(api_schema)
    except ValueError:
        raise forms.ValidationError("Invalid OpenAPI schema.")

    servers = api_schema.get("servers", [])
    if not servers:
        raise forms.ValidationError("No servers found in the schema.")
    if len(servers) > 1:
        raise forms.ValidationError("Multiple servers found in the schema. Only one is allowed.")
    server_url = servers[0].get("url")
    if not server_url:
        raise forms.ValidationError("No server URL found in the schema.")

    schemes = ["https", "http"] if settings.DEBUG else ["https"]
    url_validator = URLValidator(schemes=schemes)

    # Fist pass with Django's URL validator
    try:
        url_validator(server_url)
    except forms.ValidationError:
        raise forms.ValidationError("The server URL is invalid. Ensure that the URL is a valid HTTPS URL")

    try:
        validate_user_input_url(server_url, strict=not settings.DEBUG)
    except InvalidURL as e:
        raise forms.ValidationError(f"The server URL is invalid: {str(e)}")

    paths = api_schema.get("paths", {})
    if not paths:
        raise forms.ValidationError("No paths found in the schema.")

    for path in paths:
        try:
            url_validator(urljoin(server_url, path))
        except forms.ValidationError:
            raise forms.ValidationError(f"Invalid path: {path}")
    return api_schema
