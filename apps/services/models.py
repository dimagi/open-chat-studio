import itertools
from abc import ABC
from typing import Type

from django import forms
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.services import forms
from apps.teams.models import BaseTeamModel


class Subtype(models.TextChoices):
    @property
    def form_cls(self) -> Type[forms.ServiceConfigForm]:
        raise NotImplementedError


class ServiceType(models.TextChoices):
    LLM_PROVIDER = "llm_provider", _("LLM Provider")

    @classmethod
    def subtype_choices(cls):
        return list(itertools.chain.from_iterable([member.subtype.choices for member in cls]))

    @property
    def subtype(self) -> Type[Subtype]:
        match self:
            case ServiceType.LLM_PROVIDER:
                return LlmProvider

        raise Exception(f"No subtype configured for {self}")


class LlmProvider(Subtype):
    openai = "openai", _("OpenAI")
    azure = "azure", _("Azure OpenAI")

    @property
    def form_cls(self) -> Type[forms.ServiceConfigForm]:
        match self:
            case LlmProvider.openai:
                return forms.OpenAIConfigForm
            case LlmProvider.azure:
                return forms.AzureOpenAIConfigForm
        raise Exception(f"No config form configured for {self}")


class ServiceConfig(BaseTeamModel):
    service_type = models.CharField(max_length=255, choices=ServiceType.choices)
    subtype = models.CharField(max_length=255, choices=ServiceType.subtype_choices())
    name = models.CharField(max_length=255)
    config = models.JSONField(default=dict)

    def __str__(self):
        return f"{self.name} ({self.service_type}[{self.subtype}])"

    @property
    def service_type_enum(self):
        return ServiceType(self.service_type)

    @property
    def subtype_enum(self):
        return self.service_type_enum.subtype(self.subtype)
