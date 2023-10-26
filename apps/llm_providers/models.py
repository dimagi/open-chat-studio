from django.db import models
from django.utils.translation import gettext_lazy as _
from django_cryptography.fields import encrypt

from apps.teams.models import BaseTeamModel


class LlmProviderType(models.TextChoices):
    openai = "openai", _("OpenAI")
    azure = "azure", _("Azure OpenAI")


class LlmProvider(BaseTeamModel):
    type = models.CharField(max_length=255, choices=LlmProviderType.choices)
    name = models.CharField(max_length=255)
    config = encrypt(models.JSONField(default=dict))

    class Meta:
        ordering = ("type", "name")
