from django import forms
from django.core.validators import URLValidator
from django.utils.translation import gettext_lazy as _

from apps.files.forms import BaseFileFormSet
from apps.generics.help import render_help_with_link
from apps.service_providers.models import LlmProviderModel
from apps.utils.json import PrettyJSONEncoder


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
                initial[field] = self.obfusticate_field(field, initial.get(field, ""))
            kwargs["initial"] = initial
        super().__init__(*args, **kwargs)

    def obfusticate_field(self, field, initial_value):
        return obfuscate_value(initial_value)

    def clean(self):
        cleaned_data = super().clean()

        if not self.initial:
            return cleaned_data

        for field in self.obfuscate_fields:
            initial_masked = self.initial.get(field)
            if self.cleaned_data.get(field) == initial_masked:
                # If the cleaned data is the same as the initial masked value, we keep initial unmasked value
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


class OpenAIGenericConfigForm(ObfuscatingMixin, ProviderTypeConfigForm):
    obfuscate_fields = ["openai_api_key"]

    # keep naming the same for the sake of simplicity with the client
    openai_api_key = forms.CharField(label=_("API Key"))


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


class OpenAICustomVoiceFileFormset(BaseFileFormSet):
    """
    File formset for OpenAI Custom Voice audio samples.
    Validates file extension, size, and provides guidance on duration limits.
    """

    accepted_file_types = ["mp3", "wav", "ogg", "aac", "flac", "webm", "mp4", "mpeg"]
    max_file_size_mb = 10

    def clean(self) -> None:
        invalid_extensions = set()
        oversized_files = []

        for _key, in_memory_file in self.files.items():
            # Validate file extension
            file_extension = in_memory_file.name.rsplit(".", 1)[-1].lower()
            if file_extension not in self.accepted_file_types:
                invalid_extensions.add(f".{file_extension}")

            # Validate file size
            file_size_mb = in_memory_file.size / (1024 * 1024)
            if file_size_mb > self.max_file_size_mb:
                oversized_files.append(f"{in_memory_file.name} ({file_size_mb:.1f}MB)")

        errors = []
        if invalid_extensions:
            valid_types = ", ".join(f".{t}" for t in self.accepted_file_types)
            errors.append(f"File extensions not supported: {', '.join(invalid_extensions)}. Accepted: {valid_types}")

        if oversized_files:
            errors.append(f"Files exceed {self.max_file_size_mb}MB limit: {', '.join(oversized_files)}")

        if errors:
            raise forms.ValidationError(errors)

        return super().clean()


class OpenAICustomVoiceConfigForm(OpenAIConfigForm):
    """
    Configuration form for OpenAI Custom Voice provider.
    Extends OpenAIConfigForm with file upload support for voice samples.
    """

    allow_file_upload = True
    file_formset_form = OpenAICustomVoiceFileFormset


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


class GoogleGeminiConfigForm(ObfuscatingMixin, ProviderTypeConfigForm):
    obfuscate_fields = ["google_api_key"]

    google_api_key = forms.CharField(label=_("API Key"))


class GoogleVertexAIConfigForm(ObfuscatingMixin, ProviderTypeConfigForm):
    obfuscate_fields = ["credentials_json"]
    api_transport = forms.ChoiceField(
        label=_("API Transport"),
        choices=[("grpc", "gRPC"), ("rest", "REST")],
        help_text=_(
            "Advanced parameter to select the transport protocol for the API calls. "
            "Refer to the Google documentation for details."
        ),
        initial="grpc",
    )
    location = forms.CharField(
        label=_("Google Cloud Platform Location"),
        help_text=render_help_with_link(
            _("Model availability may vary by region, see "),
            "https://docs.cloud.google.com/vertex-ai/generative-ai/docs/learn/locations#google_model_endpoint_locations",
            link_text="Google documentation",
            line_break=False,
        ),
        initial="global",
    )

    credentials_json = forms.JSONField(
        # expect credentials to be ~13 lines of JSON
        label=_("Service Account Key (JSON)"),
        encoder=PrettyJSONEncoder,
        widget=forms.Textarea(attrs={"rows": "13"}),
        help_text=render_help_with_link(
            _("For more details see "),
            "https://docs.cloud.google.com/iam/docs/service-account-creds#key-types",
            link_text="Google documentation",
            line_break=False,
        ),
    )

    def obfusticate_field(self, field, initial_value):
        if not initial_value:
            return initial_value
        if field == "credentials_json" and isinstance(initial_value, dict):
            initial_value = initial_value.copy()
            for key in initial_value:
                if key != "private_key_id":
                    initial_value[key] = "***"
        return super().obfusticate_field(field, str(initial_value))


class DeepSeekConfigForm(ObfuscatingMixin, ProviderTypeConfigForm):
    obfuscate_fields = ["deepseek_api_key"]

    deepseek_api_key = forms.CharField(label=_("API Key"))


def obfuscate_value(value):
    if value and isinstance(value, str):
        return value[:4] + "..." + value[-2:]
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


class BasicAuthConfigForm(ObfuscatingMixin, ProviderTypeConfigForm):
    obfuscate_fields = ["password"]

    username = forms.CharField(label=_("Username"))
    password = forms.CharField(label=_("Password"))


class ApiKeyAuthConfigForm(ObfuscatingMixin, ProviderTypeConfigForm):
    obfuscate_fields = ["value"]

    key = forms.CharField(label=_("Header Name"))
    value = forms.CharField(label=_("API Key"))


class BearerAuthConfigForm(ObfuscatingMixin, ProviderTypeConfigForm):
    obfuscate_fields = ["token"]

    token = forms.CharField(label=_("Bearer Token"))


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

    secret_key = forms.CharField(label=_("Secret Key"))
    public_key = forms.CharField(label=_("Public Key"))
    host = forms.URLField(label=_("Host"))


class LlmProviderModelForm(forms.ModelForm):
    class Meta:
        model = LlmProviderModel
        fields = ("type", "name", "max_token_limit")
        widgets = {
            "type": forms.HiddenInput(),
        }

    def __init__(self, team, *args, **kwargs):
        self.team = team
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        name = cleaned_data.get("name")
        max_token_limit = cleaned_data.get("max_token_limit")

        if (
            LlmProviderModel.objects.filter(team=self.team, name=name, max_token_limit=max_token_limit)
            .exclude(pk=self.instance.pk if self.instance else None)
            .exists()
        ):
            raise forms.ValidationError(_("A model with this name and max token limit already exists for your team"))

        return cleaned_data
