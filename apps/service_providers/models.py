from typing import List, Type

from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_cryptography.fields import encrypt
from pydantic import ValidationError

from apps.channels.models import ChannelPlatform
from apps.teams.models import BaseTeamModel

from . import forms, llm_service, messaging_service, speech_service
from .exceptions import ServiceProviderConfigError


class LlmProviderType(models.TextChoices):
    openai = "openai", _("OpenAI")
    azure = "azure", _("Azure OpenAI")

    @property
    def form_cls(self) -> Type[forms.ProviderTypeConfigForm]:
        match self:
            case LlmProviderType.openai:
                return forms.OpenAIConfigForm
            case LlmProviderType.azure:
                return forms.AzureOpenAIConfigForm
        raise Exception(f"No config form configured for {self}")

    def get_llm_service(self, config: dict):
        try:
            match self:
                case LlmProviderType.openai:
                    return llm_service.OpenAILlmService(**config)
                case LlmProviderType.azure:
                    return llm_service.AzureLlmService(**config)
        except ValidationError as e:
            raise ServiceProviderConfigError(self, str(e)) from e
        raise ServiceProviderConfigError(self, "No chat model configured")


class LlmProvider(BaseTeamModel):
    team = models.ForeignKey("teams.Team", on_delete=models.CASCADE)
    type = models.CharField(max_length=255, choices=LlmProviderType.choices)
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
        return LlmProviderType(self.type)

    def get_llm_service(self):
        config = {k: v for k, v in self.config.items() if v}
        return self.type_enum.get_llm_service(config)


class VoiceProviderType(models.TextChoices):
    aws = "aws", _("AWS Polly")
    azure = "azure", _("Azure Text to Speech")

    @property
    def form_cls(self) -> Type[forms.ProviderTypeConfigForm]:
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


class VoiceProvider(BaseTeamModel):
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

    @property
    def form_cls(self) -> Type[forms.ProviderTypeConfigForm]:
        match self:
            case MessagingProviderType.twilio:
                return forms.TwilioMessagingConfigForm
        raise Exception(f"No config form configured for {self}")

    def get_messaging_service(self, config: dict) -> messaging_service.MessagingService:
        match self:
            case MessagingProviderType.twilio:
                return messaging_service.TwilioService(**config)
        raise Exception(f"No messaging service configured for {self}")

    @staticmethod
    def platform_supported_provider_types(platform: ChannelPlatform) -> List["MessagingProviderType"]:
        """Finds all provider types supporting the platform specified by `platform`"""
        provider_types = []
        for service in messaging_service.MessagingService.__subclasses__():
            if platform in service.supported_platforms:
                provider_types.append(MessagingProviderType(service._type))
        return provider_types


class MessagingProvider(BaseTeamModel):
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
