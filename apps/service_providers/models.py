from typing import Type

from django.db import models
from django.utils.translation import gettext_lazy as _
from django_cryptography.fields import encrypt
from langchain.chat_models import ChatOpenAI
from langchain.llms import AzureOpenAI

from apps.teams.models import BaseTeamModel

from . import forms, voice_service


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

    @property
    def chat_model_cls(self):
        match self:
            case LlmProviderType.openai:
                return ChatOpenAI
            case LlmProviderType.azure:
                return AzureOpenAI
        raise Exception(f"No chat model configured for {self}")


class LlmProvider(BaseTeamModel):
    team = models.ForeignKey("teams.Team", on_delete=models.CASCADE, related_name="llm_providers_new")
    type = models.CharField(max_length=255, choices=LlmProviderType.choices)
    name = models.CharField(max_length=255)
    config = encrypt(models.JSONField(default=dict))

    class Meta:
        ordering = ("type", "name")

    def __str__(self):
        return f"{self.type_enum.label}: {self.name}"

    @property
    def type_enum(self):
        return LlmProviderType(self.type)

    def get_chat_model(self, llm_model: str, temperature: float):
        config = {k: v for k, v in self.config.items() if v}
        return self.type_enum.chat_model_cls(model=llm_model, temperature=temperature, **config)


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

    def get_voice_service(self, config: dict):
        match self:
            case VoiceProviderType.aws:
                return voice_service.AWSVoiceSynthesizer(**config)
            case VoiceProviderType.azure:
                return voice_service.AzureVoiceSynthesizer(**config)
        raise Exception(f"No voice service configured for {self}")


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

    def get_voice_service(self):
        return self.type_enum.get_voice_service(self.config)
