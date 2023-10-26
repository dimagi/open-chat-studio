import functools
from enum import Enum

from django import forms

from apps.generics.type_select_form import TypeSelectForm

from .models import LlmProvider, LlmProviderType, VoiceProvider, VoiceProviderType
from .tables import LlmProviderTable, VoiceProviderTable


class ServiceProvider(Enum):
    llm = "llm"
    voice = "voice"

    @property
    def model(self):
        match self:
            case ServiceProvider.llm:
                return LlmProvider
            case ServiceProvider.voice:
                return VoiceProvider
        raise ValueError(f"Invalid provider type: {self}")

    @property
    def table(self):
        match self:
            case ServiceProvider.llm:
                return LlmProviderTable
            case ServiceProvider.voice:
                return VoiceProviderTable
        raise ValueError(f"Invalid provider type: {self}")

    @property
    def subtype(self):
        """Return the enum for the subtypes of this provider type.

        It is required that the enum has a `form_cls` property which returns
        the config form class for that subtype."""
        match self:
            case ServiceProvider.llm:
                return LlmProviderType
            case ServiceProvider.voice:
                return VoiceProviderType
        raise ValueError(f"Invalid provider type: {self}")

    @property
    def primary_fields(self):
        return ["name", "type"]

    @property
    def secondary_key_field(self):
        return "type"

    def get_form_initial(self, instance):
        return instance.config


def get_service_provider_config_form(provider: ServiceProvider, data=None, instance=None) -> TypeSelectForm:
    initial_config = provider.get_form_initial(instance) if instance else None
    return TypeSelectForm(
        primary=_get_main_form(provider, data=data.copy() if data else None, instance=instance),
        secondary={
            type_: type_.form_cls(data=data.copy() if data else None, initial=initial_config)
            for type_ in provider.subtype
        },
        secondary_key_field=provider.secondary_key_field,
    )


def _get_main_form(provider: ServiceProvider, instance=None, data=None):
    form_cls = forms.modelform_factory(
        provider.model,
        fields=provider.primary_fields,
        formfield_callback=functools.partial(formfield_for_dbfield, provider=provider),
    )
    form = form_cls(data=data, instance=instance)
    if instance:
        form.fields[provider.secondary_key_field].disabled = True

    return form


def formfield_for_dbfield(db_field, provider, **kwargs):
    if db_field.name == provider.secondary_key_field:
        # remove 'empty' value from choices
        return forms.TypedChoiceField(empty_value=None, choices=provider.subtype.choices)
    return db_field.formfield(**kwargs)
