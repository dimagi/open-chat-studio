import dataclasses
from enum import Enum
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, models, transaction
from django.urls import reverse
from django.utils.functional import classproperty
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django_cryptography.fields import encrypt
from field_audit import audit_fields
from field_audit.models import AuditingManager
from pydantic import ValidationError

from apps.channels.models import ChannelPlatform
from apps.experiments.models import SyntheticVoice
from apps.service_providers import auth_service, const, model_audit_fields
from apps.teams.models import BaseTeamModel, Team
from apps.utils.deletion import get_related_objects, has_related_objects

from ..teams.utils import get_slug_for_team
from .exceptions import ServiceProviderConfigError

if TYPE_CHECKING:
    from apps.service_providers import llm_service, messaging_service, speech_service, tracing
    from apps.service_providers.forms import ProviderTypeConfigForm


class MessagingProviderObjectManager(AuditingManager):
    pass


class VoiceProviderObjectManager(AuditingManager):
    pass


class LlmProviderObjectManagerObjectManager(AuditingManager):
    pass


class ProviderMixin:
    def add_files(self, *args, **kwargs): ...


@dataclasses.dataclass
class LlmProviderType:
    slug: str
    label: str
    additional_config: dict = dataclasses.field(default_factory=dict)

    def __str__(self):
        return self.slug

    @property
    def max_vector_stores(self) -> int | None:
        """Returns the maximum number of vector stores supported per request, or None if unlimited."""
        return self.additional_config.get("max_vector_stores")


class LlmProviderTypes(LlmProviderType, Enum):
    openai = (
        "openai",
        _("OpenAI"),
        {"supports_transcription": True, "supports_assistants": True, "max_vector_stores": 2},
    )
    azure = "azure", _("Azure OpenAI")
    anthropic = "anthropic", _("Anthropic")
    groq = "groq", _("Groq"), {"openai_api_base": "https://api.groq.com/openai/v1/"}
    perplexity = "perplexity", _("Perplexity"), {"openai_api_base": "https://api.perplexity.ai/"}
    deepseek = "deepseek", _("DeepSeek"), {"deepseek_api_base": "https://api.deepseek.com/v1/"}
    google = "google", _("Google Gemini")
    google_vertex_ai = "google_vertex_ai", _("Google Vertex AI")

    def __str__(self):
        return str(self.value)

    @classproperty
    def choices(cls):
        empty = [(None, cls.__empty__)] if hasattr(cls, "__empty__") else []
        return empty + [(member.value.slug, member.label) for member in cls]

    @property
    def supports_transcription(self):
        return self.additional_config.get("supports_transcription", False)

    @property
    def supports_assistants(self):
        return self.additional_config.get("supports_assistants", False)

    @property
    def max_vector_stores(self) -> int | None:
        """Returns the maximum number of vector stores supported per request, or None if unlimited."""
        return self.additional_config.get("max_vector_stores")

    @property
    def form_cls(self) -> type["ProviderTypeConfigForm"]:
        from apps.service_providers import forms

        match self:
            case LlmProviderTypes.openai:
                return forms.OpenAIConfigForm
            case LlmProviderTypes.azure:
                return forms.AzureOpenAIConfigForm
            case LlmProviderTypes.anthropic:
                return forms.AnthropicConfigForm
            case LlmProviderTypes.groq | LlmProviderTypes.perplexity:
                return forms.OpenAIGenericConfigForm
            case LlmProviderTypes.deepseek:
                return forms.DeepSeekConfigForm
            case LlmProviderTypes.google:
                return forms.GoogleGeminiConfigForm
            case LlmProviderTypes.google_vertex_ai:
                return forms.GoogleVertexAIConfigForm
        raise Exception(f"No config form configured for {self}")

    def get_llm_service(self, config: dict) -> "llm_service.LlmService":
        from . import llm_service

        config = {**config, **self.additional_config, "_type": self.slug}
        try:
            match self:
                case LlmProviderTypes.openai:
                    return llm_service.OpenAILlmService(**config)
                case LlmProviderTypes.azure:
                    return llm_service.AzureLlmService(**config)
                case LlmProviderTypes.anthropic:
                    return llm_service.AnthropicLlmService(**config)
                case LlmProviderTypes.groq | LlmProviderTypes.perplexity:
                    return llm_service.OpenAIGenericService(**config)
                case LlmProviderTypes.deepseek:
                    return llm_service.DeepSeekLlmService(**config)
                case LlmProviderTypes.google:
                    return llm_service.GoogleLlmService(**config)
                case LlmProviderTypes.google_vertex_ai:
                    return llm_service.GoogleVertexAILlmService(**config)
        except ValidationError as e:
            raise ServiceProviderConfigError(self.slug, str(e)) from e
        raise ServiceProviderConfigError(self.slug, "No chat model configured")


@audit_fields(*model_audit_fields.LLM_PROVIDER_FIELDS, audit_special_queryset_writes=True)
class LlmProvider(BaseTeamModel, ProviderMixin):
    objects = LlmProviderObjectManagerObjectManager()
    team = models.ForeignKey("teams.Team", on_delete=models.CASCADE)
    type = models.CharField(max_length=255, choices=LlmProviderTypes.choices)
    name = models.CharField(max_length=255)
    config = encrypt(models.JSONField(default=dict))

    class Meta:
        ordering = ("type", "name")

    def __str__(self):
        return f"{self.type_enum.label}: {self.name}"

    @property
    def type_enum(self):
        return LlmProviderTypes[str(self.type)]

    def get_llm_service(self) -> "llm_service.LlmService":
        config = {k: v for k, v in self.config.items() if v}
        return self.type_enum.get_llm_service(config)

    def get_remote_index_manager(self, index_id: str):
        return self.get_llm_service().get_remote_index_manager(index_id)

    def get_local_index_manager(self, embedding_model_name: str):
        """
        Returns a LocalIndexManager for the given embedding model.
        """
        return self.get_llm_service().get_local_index_manager(embedding_model_name)

    def create_remote_index(self, name: str, file_ids: list = None) -> str:
        """
        Creates a remote index with the given name and returns its ID.
        If file_ids are provided, they will be linked to the index.
        """
        return self.get_llm_service().create_remote_index(name, file_ids)


class LlmProviderModelManager(models.Manager):
    def get_or_create_for_team(self, team, name, type, max_token_limit=8192):
        try:
            return self.for_team(team).get(name=name, type=type, max_token_limit=max_token_limit), False
        except LlmProviderModel.DoesNotExist:
            return self.create(
                team=team,
                name=name,
                type=type,
                max_token_limit=max_token_limit,
            ), True

    def for_team(self, team: int | Team):
        if isinstance(team, Team):
            team = team.id
        return super().get_queryset().filter(models.Q(team_id=team) | models.Q(team__isnull=True))


class LlmProviderModel(BaseTeamModel):
    team = models.ForeignKey(
        Team,
        verbose_name=gettext("Team"),
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )  # Optional FK relationship to team. If this is
    # null, then it is a "global" model that is managed in Django admin.

    type = models.CharField(max_length=255, choices=LlmProviderTypes.choices)

    name = models.CharField(
        max_length=128, help_text="The name of the model. e.g. 'gpt-4o-mini' or 'claude-3-5-sonnet-20240620'"
    )
    max_token_limit = models.PositiveIntegerField(
        default=8192,
        help_text="When the message history for a session exceeds this limit (in tokens), it will be compressed. "
        "If 0, compression will be disabled which may result in errors or high LLM costs.",
    )
    deprecated = models.BooleanField(default=False)

    @property
    def display_name(self):
        return f"{self.name} (Deprecated)" if self.deprecated else self.name

    objects = LlmProviderModelManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("team", "name", "type", "max_token_limit"), name="unique_team_name_type_max_token_limit"
            ),
        ]

    def __str__(self):
        label = f"{LlmProviderTypes[self.type].label}: {self.display_name}"
        if self.is_custom():
            label = f"{label} (custom for {self.team.name})"
        return label

    def is_custom(self):
        return self.team is not None

    def has_related_objects(self):
        return has_related_objects(self, "llm_provider_model_id")

    def delete(self, *args, **kwargs):
        related_objects = get_related_objects(self, "llm_provider_model_id")

        if related_objects:
            related_object_strings = [
                f"{obj._meta.verbose_name}: {getattr(obj, 'name', obj)}" for obj in related_objects
            ]
            raise DjangoValidationError(
                f"Cannot delete LLM Provider Model {self.name} "
                f"as it is in use by the following objects: {', '.join(related_object_strings)}"
            )
        return super().delete(*args, **kwargs)


class VoiceProviderType(models.TextChoices):
    aws = "aws", _("AWS Polly")
    azure = "azure", _("Azure Text to Speech")
    openai = "openai", _("OpenAI Text to Speech")
    openai_voice_engine = "openaivoiceengine", _("OpenAI Voice Engine Text to Speech")
    openai_custom_voice = "openaicustomvoice", _("OpenAI Custom Voice")

    @property
    def form_cls(self) -> type["ProviderTypeConfigForm"]:
        from apps.service_providers import forms

        match self:
            case VoiceProviderType.aws:
                return forms.AWSVoiceConfigForm
            case VoiceProviderType.azure:
                return forms.AzureVoiceConfigForm
            case VoiceProviderType.openai:
                return forms.OpenAIConfigForm
            case VoiceProviderType.openai_voice_engine:
                return forms.OpenAIVoiceEngineConfigForm
            case VoiceProviderType.openai_custom_voice:
                return forms.OpenAICustomVoiceConfigForm
        raise Exception(f"No config form configured for {self}")

    def get_speech_service(self, config: dict) -> "speech_service.SpeechService":
        from . import speech_service

        try:
            match self:
                case VoiceProviderType.aws:
                    return speech_service.AWSSpeechService(**config)
                case VoiceProviderType.azure:
                    return speech_service.AzureSpeechService(**config)
                case VoiceProviderType.openai:
                    return speech_service.OpenAISpeechService(**config)
                case VoiceProviderType.openai_voice_engine:
                    return speech_service.OpenAIVoiceEngineSpeechService(**config)
                case VoiceProviderType.openai_custom_voice:
                    return speech_service.OpenAICustomVoiceSpeechService(**config)
        except ValidationError as e:
            raise ServiceProviderConfigError(self, str(e)) from e
        raise ServiceProviderConfigError(self, "No voice service configured")


@audit_fields(*model_audit_fields.VOICE_PROVIDER_FIELDS, audit_special_queryset_writes=True)
class VoiceProvider(BaseTeamModel, ProviderMixin):
    objects = VoiceProviderObjectManager()
    type = models.CharField(max_length=255, choices=VoiceProviderType.choices)
    name = models.CharField(max_length=255)
    config = encrypt(models.JSONField(default=dict))

    class Meta:
        ordering = ("type", "name")

    def __str__(self):
        return f"{self.type_enum.label}: {self.name}"

    @property
    def type_enum(self):
        return VoiceProviderType(self.type)

    def get_speech_service(self) -> "speech_service.SpeechService":
        config = {k: v for k, v in self.config.items() if v}
        return self.type_enum.get_speech_service(config)

    def get_custom_voice_client(self):
        """
        Get OpenAI Custom Voice API client for voice management operations.
        Only available for openai_custom_voice provider type.
        """
        if self.type != VoiceProviderType.openai_custom_voice:
            raise ValueError(f"Custom voice client not available for provider type: {self.type}")

        from apps.service_providers.openai_custom_voice import OpenAICustomVoiceClient

        return OpenAICustomVoiceClient(
            api_key=self.config["openai_api_key"],
            organization=self.config.get("openai_organization"),
            base_url=self.config.get("openai_api_base"),
        )

    @transaction.atomic()
    def add_files(self, files):
        if self.type == VoiceProviderType.openai_voice_engine:
            for file in files:
                try:
                    # TODO: Split file extention
                    SyntheticVoice.objects.create(
                        name=file.name,
                        neural=True,
                        language="",
                        language_code="",
                        gender="",
                        service=SyntheticVoice.OpenAIVoiceEngine,
                        voice_provider=self,
                        file=file,
                    )
                except IntegrityError:
                    message = f"Unable to upload '{file.name}' voice. This voice might already exist"
                    raise ValidationError(message) from None
        elif self.type == VoiceProviderType.openai_custom_voice:
            # Custom voice files are handled through the voice creation workflow
            # (consent upload -> voice creation) rather than direct file upload
            pass

    def remove_file(self, file_id: int):
        synthetic_voice = self.syntheticvoice_set.get(file_id=file_id)
        synthetic_voice.file.delete()
        synthetic_voice.delete()

    def get_files(self):
        """Return the files found on the synthetic voices that points to this instance"""
        # Since the File model uses a generic FK, we cannot simply do a .values_list on a VoiceProvider query,
        # since VoiceProvider does not have a reverse relation to `File` like SyntheticVoice has
        return [sv.file for sv in self.syntheticvoice_set.filter(file__isnull=False).all()]

    def remove_file_url(self):
        return reverse(
            "service_providers:delete_file",
            kwargs={
                "team_slug": get_slug_for_team(self.team_id),
                "provider_type": const.VOICE,
                "pk": self.id,
                "file_id": "000",
            },
        )

    def add_file_url(self):
        return reverse(
            "service_providers:add_file",
            kwargs={
                "team_slug": get_slug_for_team(self.team_id),
                "provider_type": const.VOICE,
                "pk": self.id,
            },
        )

    @transaction.atomic()
    def delete(self):
        if self.type in (VoiceProviderType.openai_voice_engine, VoiceProviderType.openai_custom_voice):
            files_to_delete = self.get_files()
            [f.delete() for f in files_to_delete]
        return super().delete()


class MessagingProviderType(models.TextChoices):
    twilio = "twilio", _("Twilio")
    turnio = "turnio", _("Turn.io")
    sureadhere = "sureadhere", _("SureAdhere")
    slack = "slack", _("Slack")

    @property
    def form_cls(self) -> type["ProviderTypeConfigForm"]:
        from apps.service_providers import forms

        match self:
            case MessagingProviderType.twilio:
                return forms.TwilioMessagingConfigForm
            case MessagingProviderType.turnio:
                return forms.TurnIOMessagingConfigForm
            case MessagingProviderType.sureadhere:
                return forms.SureAdhereMessagingConfigForm
            case MessagingProviderType.slack:
                return forms.SlackMessagingConfigForm
        raise Exception(f"No config form configured for {self}")

    def get_messaging_service(self, config: dict) -> "messaging_service.MessagingService":
        from . import messaging_service

        match self:
            case MessagingProviderType.twilio:
                return messaging_service.TwilioService(**config)
            case MessagingProviderType.turnio:
                return messaging_service.TurnIOService(**config)
            case MessagingProviderType.sureadhere:
                return messaging_service.SureAdhereService(**config)
            case MessagingProviderType.slack:
                return messaging_service.SlackService(**config)
        raise Exception(f"No messaging service configured for {self}")

    @staticmethod
    def platform_supported_provider_types(platform: ChannelPlatform) -> list["MessagingProviderType"]:
        """Finds all provider types supporting the platform specified by `platform`"""
        from . import messaging_service

        provider_types = []
        for service in messaging_service.MessagingService.__subclasses__():
            if platform in service.supported_platforms:
                provider_types.append(MessagingProviderType(service._type))
        return provider_types


@audit_fields(*model_audit_fields.MESSAGING_PROVIDER_FIELDS, audit_special_queryset_writes=True)
class MessagingProvider(BaseTeamModel, ProviderMixin):
    objects = MessagingProviderObjectManager()
    type = models.CharField(max_length=255, choices=MessagingProviderType.choices)
    name = models.CharField(max_length=255)
    config = encrypt(models.JSONField(default=dict))

    class Meta:
        ordering = ("type", "name")

    def __str__(self):
        return f"{self.type_enum.label}: {self.name}"

    @property
    def type_enum(self):
        return MessagingProviderType(self.type)

    def get_messaging_service(self) -> "messaging_service.MessagingService":
        return self.type_enum.get_messaging_service(self.config)


class AuthProviderType(models.TextChoices):
    basic = "basic", _("Basic")
    api_key = "api_key", _("API Key")
    bearer = "bearer", _("Bearer Auth")
    commcare = "commcare", _("CommCare")

    @property
    def form_cls(self) -> type["ProviderTypeConfigForm"]:
        from apps.service_providers import forms

        match self:
            case AuthProviderType.basic:
                return forms.BasicAuthConfigForm
            case AuthProviderType.api_key:
                return forms.ApiKeyAuthConfigForm
            case AuthProviderType.bearer:
                return forms.BearerAuthConfigForm
            case AuthProviderType.commcare:
                return forms.CommCareAuthConfigForm
        raise Exception(f"No config form configured for {self}")

    def get_auth_service(self, config: dict) -> auth_service.AuthService:
        match self:
            case AuthProviderType.basic:
                return auth_service.BasicAuthService(**config)
            case AuthProviderType.api_key:
                return auth_service.ApiKeyAuthService(**config)
            case AuthProviderType.bearer:
                return auth_service.BearerTokenAuthService(**config)
            case AuthProviderType.commcare:
                return auth_service.CommCareAuthService(**config)
        raise Exception(f"No auth service configured for {self}")


class AuthProviderManager(AuditingManager):
    pass


@audit_fields("team", "type", "name", "config", audit_special_queryset_writes=True)
class AuthProvider(BaseTeamModel):
    objects = AuthProviderManager()
    type = models.CharField(max_length=255, choices=AuthProviderType.choices)
    name = models.CharField(max_length=255)
    config = encrypt(models.JSONField(default=dict))

    class Meta:
        ordering = ("type", "name")

    def __str__(self):
        return f"{self.type_enum.label}: {self.name}"

    @property
    def type_enum(self):
        return AuthProviderType(self.type)

    def get_auth_service(self) -> auth_service.AuthService:
        return self.type_enum.get_auth_service(self.config)


class TraceProviderType(models.TextChoices):
    langfuse = "langfuse", _("Langfuse")

    @property
    def form_cls(self) -> type["ProviderTypeConfigForm"]:
        from apps.service_providers import forms

        match self:
            case TraceProviderType.langfuse:
                return forms.LangfuseTraceProviderForm
        raise Exception(f"No config form configured for {self}")

    def get_service(self, config: dict) -> "tracing.Tracer":
        from . import tracing

        match self:
            case TraceProviderType.langfuse:
                return tracing.LangFuseTracer(self, config)
        raise Exception(f"No tracing service configured for {self}")


@audit_fields("team", "type", "name", "config", audit_special_queryset_writes=True)
class TraceProvider(BaseTeamModel):
    objects = AuditingManager()
    type = models.CharField(max_length=255, choices=TraceProviderType.choices)
    name = models.CharField(max_length=255)
    config = encrypt(models.JSONField(default=dict))

    class Meta:
        ordering = ("type", "name")

    def __str__(self):
        return f"{self.type_enum.label}: {self.name}"

    @property
    def type_enum(self):
        return TraceProviderType(self.type)

    def get_service(self) -> "tracing.Tracer":
        return self.type_enum.get_service(self.config)


class EmbeddingProviderModelManager(models.Manager):
    def for_team(self, team):
        return super().get_queryset().filter(models.Q(team=team) | models.Q(team__isnull=True))


class EmbeddingProviderModel(BaseTeamModel):
    type = models.CharField(max_length=255, choices=LlmProviderTypes.choices)
    name = models.CharField(max_length=128, help_text="The name of the model. e.g. 'text-embedding-3-small'")
    team = models.ForeignKey(
        Team,
        verbose_name=gettext("Team"),
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )

    objects = EmbeddingProviderModelManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("team", "name", "type"), name="unique_team_name_type"),
        ]

    @property
    def type_enum(self):
        return LlmProviderTypes[str(self.type)]

    def __str__(self):
        return f"{self.name}"
