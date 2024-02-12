import dataclasses
from enum import Enum

from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils.functional import classproperty
from django.utils.translation import gettext_lazy as _
from django_cryptography.fields import encrypt
from field_audit import audit_fields
from field_audit.models import AuditingManager
from pydantic import ValidationError

from apps.channels.models import ChannelPlatform
from apps.service_providers import auth_service, model_audit_fields
from apps.teams.models import BaseTeamModel

from . import forms, llm_service, messaging_service, speech_service
from .exceptions import ServiceProviderConfigError


class MessagingProviderObjectManager(AuditingManager):
    pass


class VoiceProviderObjectManager(AuditingManager):
    pass


class LlmProviderObjectManagerObjectManager(AuditingManager):
    pass


@dataclasses.dataclass
class LlmProviderType:
    slug: str
    label: str
    supports_transcription: bool = False
    supports_assistants: bool = False

    def __str__(self):
        return self.slug


class LlmProviderTypes(LlmProviderType, Enum):
    openai = "openai", _("OpenAI"), True, True
    azure = "azure", _("Azure OpenAI")
    anthropic = "anthropic", _("Anthropic")

    def __str__(self):
        return str(self.value)

    @classproperty
    def choices(cls):
        empty = [(None, cls.__empty__)] if hasattr(cls, "__empty__") else []
        return empty + [(member.value.slug, member.label) for member in cls]

    @property
    def form_cls(self) -> type[forms.ProviderTypeConfigForm]:
        match self:
            case LlmProviderTypes.openai:
                return forms.OpenAIConfigForm
            case LlmProviderTypes.azure:
                return forms.AzureOpenAIConfigForm
            case LlmProviderTypes.anthropic:
                return forms.AnthropicConfigForm
        raise Exception(f"No config form configured for {self}")

    def get_llm_service(self, config: dict):
        config = {
            "supports_assistants": self.supports_assistants,
            "supports_transcription": self.supports_transcription,
            **config,
        }
        try:
            match self:
                case LlmProviderTypes.openai:
                    return llm_service.OpenAILlmService(**config)
                case LlmProviderTypes.azure:
                    return llm_service.AzureLlmService(**config)
                case LlmProviderTypes.anthropic:
                    return llm_service.AnthropicLlmService(**config)
        except ValidationError as e:
            raise ServiceProviderConfigError(self.slug, str(e)) from e
        raise ServiceProviderConfigError(self.slug, "No chat model configured")


@audit_fields(*model_audit_fields.LLM_PROVIDER_FIELDS, audit_special_queryset_writes=True)
class LlmProvider(BaseTeamModel):
    objects = LlmProviderObjectManagerObjectManager()
    team = models.ForeignKey("teams.Team", on_delete=models.CASCADE)
    type = models.CharField(max_length=255, choices=LlmProviderTypes.choices)
    name = models.CharField(max_length=255)
    llm_models = ArrayField(
        models.CharField(max_length=128),
        default=list,
        verbose_name="LLM Models",
        help_text="The models that will be available for use. Separate multiple models with a comma.",
    )
    config = encrypt(models.JSONField(default=dict))

    class Meta:
        ordering = ("type", "name")

    def __str__(self):
        return f"{self.type_enum.label}: {self.name}"

    @property
    def type_enum(self):
        return LlmProviderTypes[str(self.type)]

    def get_llm_service(self):
        config = {k: v for k, v in self.config.items() if v}
        return self.type_enum.get_llm_service(config)


class VoiceProviderType(models.TextChoices):
    aws = "aws", _("AWS Polly")
    azure = "azure", _("Azure Text to Speech")

    @property
    def form_cls(self) -> type[forms.ProviderTypeConfigForm]:
        match self:
            case VoiceProviderType.aws:
                return forms.AWSVoiceConfigForm
            case VoiceProviderType.azure:
                return forms.AzureVoiceConfigForm
        raise Exception(f"No config form configured for {self}")

    def get_speech_service(self, config: dict):
        try:
            match self:
                case VoiceProviderType.aws:
                    return speech_service.AWSSpeechService(**config)
                case VoiceProviderType.azure:
                    return speech_service.AzureSpeechService(**config)
        except ValidationError as e:
            raise ServiceProviderConfigError(self, str(e)) from e
        raise ServiceProviderConfigError(self, "No voice service configured")


@audit_fields(*model_audit_fields.VOICE_PROVIDER_FIELDS, audit_special_queryset_writes=True)
class VoiceProvider(BaseTeamModel):
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

    def get_speech_service(self) -> speech_service.SpeechService:
        return self.type_enum.get_speech_service(self.config)


class MessagingProviderType(models.TextChoices):
    twilio = "twilio", _("Twilio")
    turnio = "turnio", _("Turn.io")

    @property
    def form_cls(self) -> type[forms.ProviderTypeConfigForm]:
        match self:
            case MessagingProviderType.twilio:
                return forms.TwilioMessagingConfigForm
            case MessagingProviderType.turnio:
                return forms.TurnIOMessagingConfigForm
        raise Exception(f"No config form configured for {self}")

    def get_messaging_service(self, config: dict) -> messaging_service.MessagingService:
        match self:
            case MessagingProviderType.twilio:
                return messaging_service.TwilioService(**config)
            case MessagingProviderType.turnio:
                return messaging_service.TurnIOService(**config)
        raise Exception(f"No messaging service configured for {self}")

    @staticmethod
    def platform_supported_provider_types(platform: ChannelPlatform) -> list["MessagingProviderType"]:
        """Finds all provider types supporting the platform specified by `platform`"""
        provider_types = []
        for service in messaging_service.MessagingService.__subclasses__():
            if platform in service.supported_platforms:
                provider_types.append(MessagingProviderType(service._type))
        return provider_types


@audit_fields(*model_audit_fields.MESSAGING_PROVIDER_FIELDS, audit_special_queryset_writes=True)
class MessagingProvider(BaseTeamModel):
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

    def get_messaging_service(self) -> messaging_service.MessagingService:
        return self.type_enum.get_messaging_service(self.config)


class AuthProviderType(models.TextChoices):
    commcare = "commcare", _("CommCare")

    @property
    def form_cls(self) -> type[forms.ProviderTypeConfigForm]:
        match self:
            case AuthProviderType.commcare:
                return forms.CommCareAuthConfigForm
        raise Exception(f"No config form configured for {self}")

    def get_auth_service(self, config: dict) -> auth_service.AuthService:
        match self:
            case AuthProviderType.commcare:
                return auth_service.CommCareAuthService(**config)
        raise Exception(f"No messaging service configured for {self}")


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
