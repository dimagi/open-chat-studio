import itertools
from typing import Type

from django import forms
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.services import forms
from apps.teams.models import BaseTeamModel


class ServiceType(models.TextChoices):
    LLM_PROVIDER = "llm_provider", _("LLM Provider")

    @classmethod
    def subtype_choices(cls):
        return list(itertools.chain.from_iterable([member.subtype.choices for member in cls]))

    @property
    def subtype(self) -> Type[models.TextChoices]:
        match self:
            case ServiceType.LLM_PROVIDER:
                return LlmProvider

        raise Exception(f"No subtype configured for {self}")


class LlmProvider(models.TextChoices):
    openai = "openai", _("OpenAI")
    azure = "azure", _("Azure OpenAI")

    @property
    def config_form(self):
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

    def get_forms(self, data: dict = None):
        return get_service_form(self.subtype_enum, instance=self, data=data)


def get_service_form(subtype, instance=None, data=None):
    main_form = forms.modelform_factory(
        ServiceConfig,
        fields=["service_type", "subtype", "name"],
        widgets={
            "service_type": forms.HiddenInput(),
            "subtype": forms.HiddenInput(),
        },
    )(data=data, instance=instance)

    initial_config = instance.config if instance else None
    config_form = subtype.config_form(data=data, initial=initial_config)
    return [main_form, config_form]
