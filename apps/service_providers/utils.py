import dataclasses
import functools
from collections import defaultdict
from enum import Enum
from typing import Any, Literal

from django import forms
from django.db import models
from django.db.models import Field
from django_tables2 import tables

from apps.generics.type_select_form import TypeSelectForm

from . import const
from .models import (
    AuthProvider,
    AuthProviderType,
    LlmProvider,
    LlmProviderModel,
    LlmProviderTypes,
    MessagingProvider,
    MessagingProviderType,
    TraceProvider,
    TraceProviderType,
    VoiceProvider,
    VoiceProviderType,
)
from .tables import make_table


@dataclasses.dataclass
class ServiceProviderType:
    slug: str
    label: str
    model: models.Model

    """
    Enum for the subtypes of this provider type.
    It is required that the enum has a `form_cls` property which returns
    the config form class for that subtype.
    """
    subtype: Enum

    primary_fields: list[str]

    def get_permission(self, action: Literal["view", "add", "change", "delete"]) -> Any:
        assert action in ("view", "add", "change", "delete")
        return f"{self.model._meta.app_label}.{action}_{self.model._meta.model_name}"


class ServiceProvider(ServiceProviderType, Enum):
    llm = const.LLM, "LLM Service Provider", LlmProvider, LlmProviderTypes, ["name", "type"]
    voice = const.VOICE, "Voice Service Provider", VoiceProvider, VoiceProviderType, ["name", "type"]
    messaging = const.MESSAGING, "Messaging Provider", MessagingProvider, MessagingProviderType, ["name", "type"]
    auth = const.AUTH, "Authentication Provider", AuthProvider, AuthProviderType, ["name", "type"]
    tracing = const.TRACING, "Tracing Provider", TraceProvider, TraceProviderType, ["name", "type"]

    @property
    def table(self) -> tables.Table:
        return make_table(self.slug, self.label, self.model, fields=self.primary_fields)

    @property
    def provider_type_field(self) -> str:
        """The name of the model field which determines the provider type."""
        return "type"

    def get_form_initial(self, instance) -> dict:
        """Return the initial data for the config form."""
        return instance.config


def get_service_provider_config_form(
    provider: ServiceProvider, team, exclude_forms: list, data=None, instance=None
) -> TypeSelectForm:
    """Return the form for the service provider. This is a 'type select form' which will include the main form
    and the config form for the selected provider type.
    """
    initial_config = provider.get_form_initial(instance) if instance else None

    excluded_choices = [form.value for form in exclude_forms]
    main_form = _get_main_form(provider, data=data.copy() if data else None, instance=instance)

    filtered_choices = [
        choice for choice in main_form.fields[provider.provider_type_field].choices if choice[0] not in excluded_choices
    ]
    main_form.fields[provider.provider_type_field].choices = filtered_choices

    return TypeSelectForm(
        primary=main_form,
        secondary={
            str(subtype): subtype.form_cls(team=team, data=data.copy() if data else None, initial=initial_config)
            for subtype in provider.subtype
            if subtype not in exclude_forms
        },
        secondary_key_field=provider.provider_type_field,
    )


def _get_main_form(provider: ServiceProvider, instance=None, data=None):
    """Get the main 'model form' for the service provider which will be used to create the model instance."""
    form_cls = forms.modelform_factory(
        provider.model,
        fields=provider.primary_fields,
        formfield_callback=functools.partial(formfield_for_dbfield, provider=provider),
    )
    form = form_cls(data=data, instance=instance)
    if instance:
        form.fields[provider.provider_type_field].disabled = True

    return form


def formfield_for_dbfield(db_field: Field, provider: ServiceProvider, **kwargs):
    """This is a callback function used by Django's `modelform_factory` to create the form fields for the model.
    It's use here is to customize the `provider_type` field."""
    if db_field.name == provider.provider_type_field:
        # remove 'empty' value from choices
        return forms.TypedChoiceField(empty_value=None, choices=provider.subtype.choices)
    return db_field.formfield(**kwargs)


def get_llm_provider_choices(team) -> dict[int, dict[str, list[dict[str, Any]]]]:
    providers = {}
    provider_models_by_type = defaultdict(list)
    for provider_model in LlmProviderModel.objects.for_team(team):
        provider_models_by_type[provider_model.type].append(
            {
                "value": provider_model.id,
                "text": str(provider_model),
            }
        )

    if not provider_models_by_type:
        return {}

    for provider in team.llmprovider_set.all():
        providers[provider.id] = {
            "models": provider_models_by_type[provider.type],
            "supports_assistants": provider.type_enum.supports_assistants,
        }
    return providers
