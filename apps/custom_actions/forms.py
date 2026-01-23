from functools import cached_property
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
    server_url = forms.URLField(
        label="Base URL",
        help_text="The base URL of the API server. This will be used to generate the API calls for the LLM.",
        required=True,
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
    healthcheck_path = forms.CharField(
        required=False,
        label="Health Check Path",
        help_text=(
            "Optional endpoint to check server health status. If left blank, will auto-detect from API schema if "
            "available (e.g., /health, /healthz)."
        ),
    )
    allowed_operations = forms.MultipleChoiceField(widget=forms.CheckboxSelectMultiple, required=False)

    class Meta:
        model = CustomAction
        fields = (
            "name",
            "description",
            "auth_provider",
            "prompt",
            "server_url",
            "healthcheck_path",
            "api_schema",
            "allowed_operations",
        )

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
        self.fields["server_url"].validators = [self.url_validator]

    def clean_server_url(self):
        server_url = self.cleaned_data["server_url"]

        try:
            validate_user_input_url(server_url, strict=not settings.DEBUG)
        except InvalidURL as e:
            raise forms.ValidationError(f"The server URL is invalid: {str(e)}") from None

        return server_url

    def clean_api_schema(self):
        api_schema = self.cleaned_data["api_schema"]
        return validate_api_schema(api_schema)

    def clean(self):
        if self.errors:
            return

        schema = self.cleaned_data.get("api_schema")
        operations = self.cleaned_data.get("allowed_operations")
        if schema is None or operations is None:
            return self.cleaned_data

        server_url = self.cleaned_data["server_url"]
        validate_api_schema_full(operations, schema, server_url, self.url_validator)

        # Auto-detect health endpoint from API spec if not manually provided
        healthcheck_path = self.cleaned_data.get("healthcheck_path")
        if not healthcheck_path and schema and server_url:
            # Create a temporary instance to use the detection method
            temp_action = CustomAction(api_schema=schema, server_url=server_url)
            detected_endpoint = temp_action.detect_health_endpoint_from_spec()
            if detected_endpoint:
                self.cleaned_data["healthcheck_path"] = detected_endpoint

        return {**self.cleaned_data, "allowed_operations": operations}

    @cached_property
    def url_validator(self):
        schemes = ["https", "http"] if settings.DEBUG else ["https"]
        return URLValidator(schemes=schemes)


def validate_api_schema(api_schema):
    """Perform very basic validation on the API schema."""

    try:
        OpenAPISpec.from_spec_dict(api_schema)
    except ValueError:
        raise forms.ValidationError("Invalid OpenAPI schema.") from None

    paths = api_schema.get("paths", {})
    if not paths:
        raise forms.ValidationError("No paths found in the schema.")

    api_schema.pop("servers", None)

    return api_schema


def validate_api_schema_full(operations, schema, server_url, url_validator):
    from apps.chat.agent.openapi_tool import openapi_spec_op_to_function_def

    spec = OpenAPISpec.from_spec_dict(schema)
    operations_by_id = {op.operation_id: op for op in get_operations_from_spec(spec)}
    invalid_operations = set(operations) - set(operations_by_id)
    if invalid_operations:
        raise forms.ValidationError({
            "allowed_operations": f"Invalid operations selected: {', '.join(sorted(invalid_operations))}"
        })
    for op_id in operations:
        op = operations_by_id[op_id]

        try:
            url_validator(urljoin(server_url, op.path))
        except forms.ValidationError:
            raise forms.ValidationError(f"Invalid path: {op.path}") from None

        try:
            openapi_spec_op_to_function_def(spec, op.path, op.method)
        except ValueError as e:
            raise forms.ValidationError({"allowed_operations": f"The '{op}' operation is not supported ({e})"}) from e
