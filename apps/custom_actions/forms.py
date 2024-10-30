from django import forms

from apps.custom_actions.fields import JSONORYAMLField
from apps.custom_actions.models import CustomAction


class CustomActionForm(forms.ModelForm):
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": "3"}), required=False)
    prompt = forms.CharField(
        widget=forms.Textarea(attrs={"rows": "3"}),
        required=False,
        label="Additional Prompt",
        help_text="Use this field to provide additional instructions to the LLM",
    )
    api_schema = JSONORYAMLField(
        widget=forms.Textarea(attrs={"rows": "10"}),
        required=True,
        label="API Schema",
        help_text="Paste in the OpenAPI schema for the API you want to interact with. "
        "This will be used to generate the API calls for the LLM. Accepts YAML or JSON.",
        initial={},
    )

    class Meta:
        model = CustomAction
        fields = ("name", "description", "prompt", "api_schema")
