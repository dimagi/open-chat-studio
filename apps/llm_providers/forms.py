from django import forms
from django.utils.translation import gettext_lazy as _

from apps.generics.forms import OptionalForm


class ProviderTypeConfigForm(OptionalForm):
    def save(self, instance):
        instance.config = self.cleaned_data
        return instance


class OpenAIConfigForm(ProviderTypeConfigForm):
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


class AzureOpenAIConfigForm(ProviderTypeConfigForm):
    type_key = "azure"
    openai_api_key = forms.CharField(label=_("Azure API Key"))
    openai_api_base = forms.URLField(
        label="API URL",
        help_text="Base URL path for API requests e.g. 'https://<your-endpoint>.openai.azure.com/'",
    )
