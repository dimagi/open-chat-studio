from django import forms
from django.utils.translation import gettext_lazy as _

BIND_DISABLED_TYPE_ATTRS = {"x-bind:disabled": "type !== '{key}'"}
BIND_REQUIRED_TYPE_ATTRS = {"x-bind:required": "type === '{key}'", **BIND_DISABLED_TYPE_ATTRS}


def _format_attrs(attrs, key):
    return {name: value.format(key=key) for name, value in attrs.items()}


class OptionalForm(forms.Form):
    """This class is used as a base class for forms that will be used by the 'combined_object_form.html' template.
    It adds the 'x-bind:required' attribute to required form fields so that they are only marked required when
    they are visible."""

    type_key = None

    def __init__(self, *args, **kwargs):
        assert self.type_key is not None, "type_key must be set on OptionalForm subclasses"
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if field.required:
                field.widget.attrs = _format_attrs(BIND_REQUIRED_TYPE_ATTRS, self.type_key)
            else:
                field.widget.attrs = _format_attrs(BIND_DISABLED_TYPE_ATTRS, self.type_key)

    def save(self, instance):
        raise NotImplementedError


class ServiceConfigForm(OptionalForm):
    def save(self, instance):
        instance.config = self.cleaned_data
        return instance


class OpenAIConfigForm(ServiceConfigForm):
    type_key = "openai"
    openai_api_key = forms.CharField(label=_("API Key"))
    openai_api_base = forms.URLField(
        label="API Base URL",
        required=False,
        help_text="Base URL path for API requests. Leave blank for the default API endpoint.",
    )
    openai_organization = forms.CharField(
        label="Organization ID",
        required=False,
        help_text=_(
            "This allows you to specify which OpenAI organization to use for your API requests if you belong"
            " to multiple organizations. It is not required if you only belong to"
            " one organization or want to use your default organization."
        ),
    )


class AzureOpenAIConfigForm(ServiceConfigForm):
    type_key = "azure"
    openai_api_key = forms.CharField(label=_("Azure API Key"))
    openai_api_base = forms.URLField(
        label="API URL",
        help_text="Base URL path for API requests e.g. 'https://<your-endpoint>.openai.azure.com/'",
    )
