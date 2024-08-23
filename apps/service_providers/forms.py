from django import forms
from django.core.validators import URLValidator
from django.utils.translation import gettext_lazy as _

from apps.files.forms import BaseFileFormSet


class ProviderTypeConfigForm(forms.Form):
    """
    Base class for provider type forms.

    Attributes relating to file uploads:
    allow_file_upload: If `True`, the user will be able to upload files when creating the provider. Please note
        that you have to implement the `add_files` method on your provider when this is `True`; otherwise nothing
        will happen. See `ProviderMixin` in `apps/service_providers/models.py`
    file_formset_form: This form should be a subclass of `BaseFileFormSet`. This allows you to perform provider
    specific file validation (like checking file extentions etc). If this is `None` and `allow_file_upload` is
    `True`, then the `BaseFileFormSet` class will be used by default.
    """

    allow_file_upload = False
    file_formset_form = None

    def __init__(self, team, *args, **kwargs):
        self.team = team
        super().__init__(*args, **kwargs)

    def save(self, instance):
        instance.config = self.cleaned_data
        return instance


class ObfuscatingMixin:
    """Mixin that obfuscate configured field values for display."""

    obfuscate_fields = []

    def __init__(self, *args, **kwargs):
        self.initial_raw = kwargs.get("initial")
        if self.initial_raw:
            initial = self.initial_raw.copy()
            for field in self.obfuscate_fields:
                initial[field] = obfuscate_value(initial.get(field, ""))
            kwargs["initial"] = initial
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()

        if not self.initial:
            return cleaned_data

        for field in self.obfuscate_fields:
            initial = self.initial.get(field)
            new = obfuscate_value(self.cleaned_data.get(field))
            if new == initial:
                cleaned_data[field] = self.initial_raw.get(field)

        return cleaned_data


class OpenAIConfigForm(ObfuscatingMixin, ProviderTypeConfigForm):
    obfuscate_fields = ["openai_api_key"]

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


class OpenAIVoiceEngineFileFormset(BaseFileFormSet):
    accepted_file_types = ["mp4", "mp3"]

    def clean(self) -> None:
        invalid_extentions = set()
        for _key, in_memory_file in self.files.items():
            file_extention = in_memory_file.name.split(".")[1]
            if file_extention not in self.accepted_file_types:
                invalid_extentions.add(f".{file_extention}")
        if invalid_extentions:
            string = ", ".join(invalid_extentions)
            raise forms.ValidationError(f"File extentions not supported: {string}")
        return super().clean()


class OpenAIVoiceEngineConfigForm(OpenAIConfigForm):
    allow_file_upload = True
    file_formset_form = OpenAIVoiceEngineFileFormset


class AzureOpenAIConfigForm(ObfuscatingMixin, ProviderTypeConfigForm):
    obfuscate_fields = ["openai_api_key"]

    openai_api_key = forms.CharField(label=_("Azure API Key"))
    openai_api_base = forms.URLField(
        label="API URL",
        help_text="Base URL path for API requests e.g. 'https://<your-endpoint>.openai.azure.com/'",
    )
    openai_api_version = forms.CharField(
        label="API Version",
        help_text="API Version e.g. '2023-05-15'",
    )


class AnthropicConfigForm(ObfuscatingMixin, ProviderTypeConfigForm):
    obfuscate_fields = ["anthropic_api_key"]

    anthropic_api_key = forms.CharField(label=_("Anthropic API Key"))
    anthropic_api_base = forms.URLField(
        label="API URL",
        help_text="Base URL path for API requests e.g. 'https://api.anthropic.com'",
        initial="https://api.anthropic.com",
    )


def obfuscate_value(value):
    if value and isinstance(value, str):
        return value[:4] + "*" * (len(value) - 4)
    return value


class AWSVoiceConfigForm(ObfuscatingMixin, ProviderTypeConfigForm):
    obfuscate_fields = ["aws_secret_access_key"]

    aws_access_key_id = forms.CharField(label=_("Access Key ID"))
    aws_secret_access_key = forms.CharField(label=_("Secret Access Key"))
    aws_region = forms.CharField(label=_("Region"))


class AzureVoiceConfigForm(ObfuscatingMixin, ProviderTypeConfigForm):
    obfuscate_fields = ["azure_subscription_key"]

    azure_subscription_key = forms.CharField(label=_("Subscription Key"))
    azure_region = forms.CharField(label=_("Region"))


class TwilioMessagingConfigForm(ObfuscatingMixin, ProviderTypeConfigForm):
    obfuscate_fields = ["auth_token"]

    account_sid = forms.CharField(label=_("Account SID"))
    auth_token = forms.CharField(label=_("Auth Token"))


class TurnIOMessagingConfigForm(ObfuscatingMixin, ProviderTypeConfigForm):
    obfuscate_fields = ["auth_token"]

    auth_token = forms.CharField(label=_("Auth Token"))


class SureAdhereMessagingConfigForm(ObfuscatingMixin, ProviderTypeConfigForm):
    obfuscate_fields = ["client_secret"]

    client_id = forms.CharField(
        label=_("Client ID"), help_text=_("Azure AD B2C Application ID used for authentication.")
    )
    client_secret = forms.CharField(
        label=_("Client Secret"), help_text=_("Secret used for authentication with Azure AD B2C.")
    )
    client_scope = forms.CharField(
        label=_("Client Scope"), help_text=_("Scope used for authentication with Azure AD B2C.")
    )
    auth_url = forms.URLField(
        label=_("Auth URL"),
        validators=[URLValidator(schemes=["https"])],
        help_text=_("URL used for authentication with Azure AD B2C."),
    )
    base_url = forms.URLField(
        label=_("Base URL"),
        validators=[URLValidator(schemes=["https"])],
        help_text=_("URL of the SureAdhere backend server"),
    )



class CommCareAuthConfigForm(ObfuscatingMixin, ProviderTypeConfigForm):
    obfuscate_fields = ["api_key"]

    username = forms.CharField(label=_("Username"))
    api_key = forms.CharField(label=_("API Key"))


class SlackMessagingConfigForm(ProviderTypeConfigForm):
    custom_template = "service_providers/slack_config_form.html"

    slack_team_id = forms.CharField(widget=forms.HiddenInput())
    slack_installation_id = forms.CharField(widget=forms.HiddenInput())

    def get_slack_installation(self):
        from apps.slack.models import SlackInstallation

        if team_id := self.initial.get("slack_team_id"):
            return SlackInstallation.objects.filter(slack_team_id=team_id).first()


class LangfuseTraceProviderForm(ObfuscatingMixin, ProviderTypeConfigForm):
    obfuscate_fields = ["secret_key"]

    public_key = forms.CharField(label=_("Public Key"))
    secret_key = forms.CharField(label=_("Secret Key"))
    host = forms.URLField(label=_("Host"))
