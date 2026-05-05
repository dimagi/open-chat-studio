import dataclasses
import functools
from collections import defaultdict
from enum import Enum
from typing import Any, Literal

from django import forms
from django.conf import settings
from django.db import models
from django.db.models import Field
from django.http import HttpRequest
from django_tables2 import tables
from waffle import flag_is_active

from . import const
from .llm_service.default_models import get_default_model
from .models import (
    AuthProvider,
    AuthProviderType,
    EmbeddingProviderModel,
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
    voice = const.VOICE, "Speech Service Provider", VoiceProvider, VoiceProviderType, ["name", "type"]
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


def get_available_subtypes(provider: ServiceProvider, request: HttpRequest) -> list:
    """Return the subtypes for ``provider`` available to the given request.

    Filters out subtypes gated by feature flags / settings.
    """
    excluded = set()
    if provider == ServiceProvider.voice and not flag_is_active(request, "flag_open_ai_voice_engine"):
        excluded.add(VoiceProviderType.openai_voice_engine)
    if provider == ServiceProvider.messaging and not settings.SLACK_ENABLED:
        excluded.add(MessagingProviderType.slack)
    return [subtype for subtype in provider.subtype if subtype not in excluded]


def get_service_provider_forms(
    provider: ServiceProvider,
    team,
    subtype,
    *,
    data=None,
    instance=None,
):
    """Return ``(primary_form, config_form)`` for a service provider create/edit.

    ``subtype`` is the enum member identifying which config form to build.
    On edit, the subtype is derived from the instance by the caller.
    """
    initial_config = provider.get_form_initial(instance) if instance else None
    primary_form = _get_main_form(provider, instance=instance, data=data, fixed_subtype=subtype)
    config_form = subtype.form_cls(
        team=team,
        data=data.copy() if data else None,
        initial=initial_config,
    )
    return primary_form, config_form


def _get_main_form(provider: ServiceProvider, *, instance=None, data=None, fixed_subtype: Enum):
    """Get the main 'model form' for the service provider.

    The provider-type field is rendered as a hidden input with the chosen
    subtype as its initial value. On edit, it is also disabled, matching
    the previous behavior.
    """
    form_cls = forms.modelform_factory(
        provider.model,
        fields=provider.primary_fields,
        formfield_callback=functools.partial(formfield_for_dbfield, provider=provider),
    )
    initial = {provider.provider_type_field: str(fixed_subtype)}
    form = form_cls(data=data, instance=instance, initial=initial)
    type_field = form.fields[provider.provider_type_field]
    type_field.widget = forms.HiddenInput()
    if instance:
        type_field.disabled = True
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


def get_dropdown_llm_model_choices(team) -> list[tuple[str, str]]:
    """Get LLM provider model dropdown choices for the team"""
    llm_providers = LlmProvider.objects.filter(team=team).all()
    llm_provider_models_by_type = {}
    for model in LlmProviderModel.objects.for_team(team):
        llm_provider_models_by_type.setdefault(model.type, []).append(model)

    model_choices = []
    for provider in llm_providers:
        for model in llm_provider_models_by_type.get(provider.type, []):
            model_choices.append((f"{provider.id}:{model.id}", f"{provider.name} - {model!s}"))
    return model_choices


def get_embedding_provider_choices(team) -> dict[str, list[dict[str, Any]]]:
    """Group embedding models by LLM provider type for dynamic selection in forms"""
    provider_types = defaultdict(list)

    for embedding_model in EmbeddingProviderModel.objects.for_team(team):
        provider_types[embedding_model.type].append({"value": embedding_model.id, "text": str(embedding_model)})

    return provider_types


def get_first_llm_provider_by_team(team_id):
    try:
        return LlmProvider.objects.filter(team_id=team_id).order_by("id").first()
    except LlmProvider.DoesNotExist:
        return None


def get_first_llm_provider_model(llm_provider, team_id):
    try:
        if llm_provider:
            provider_models = LlmProviderModel.objects.for_team(team_id).filter(type=llm_provider.type).order_by("id")
            if default_model := get_default_model(llm_provider.type):
                provider_models = provider_models.filter(name=default_model.name)
            return provider_models.first()
        return None
    except LlmProviderModel.DoesNotExist:
        return None


def get_llm_provider_by_team(team):
    return LlmProvider.objects.filter(team=team).order_by("id")


def get_models_by_provider(provider, team):
    model_objects = LlmProviderModel.objects.for_team(team).filter(type=provider)
    return [{"value": model.id, "label": model.display_name} for model in model_objects]


def get_models_by_team_grouped_by_provider(team):
    provider_types = LlmProvider.objects.filter(team=team).values_list("type", flat=True)
    model_objects = LlmProviderModel.objects.for_team(team).filter(type__in=provider_types)

    provider_dict = defaultdict(list)
    for model in model_objects:
        type_enum = LlmProviderTypes[model.type]
        provider_label = str(type_enum.label)
        provider_dict[provider_label].append(model.display_name)

    return dict(provider_dict)
