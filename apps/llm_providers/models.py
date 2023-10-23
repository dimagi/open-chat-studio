from typing import Type

from django import forms
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_cryptography.fields import encrypt

from apps.teams.models import BaseTeamModel

from . import forms


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


class LlmProvider(BaseTeamModel):
    type = models.CharField(max_length=255, choices=LlmProviderType.choices)
    name = models.CharField(max_length=255)
    config = encrypt(models.JSONField(default=dict))

    def __str__(self):
        return f"LLM Provider {self.name} ({self.type})"

    @property
    def type_enum(self):
        return LlmProviderType(self.type)
