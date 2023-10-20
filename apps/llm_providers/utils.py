from django import forms

from ..generics.type_select_form import TypeSelectForm
from .models import LlmProvider, LlmProviderType


def get_llm_config_form(data=None, instance=None) -> TypeSelectForm:
    initial_config = instance.config if instance else None
    return TypeSelectForm(
        primary=_get_main_form(data=data.copy() if data else None, instance=instance),
        secondary={
            type_: type_.form_cls(data=data.copy() if data else None, initial=initial_config)
            for type_ in LlmProviderType
        },
        secondary_key_field="type",
    )


def _get_main_form(instance=None, data=None):
    form_cls = forms.modelform_factory(
        LlmProvider,
        fields=["name", "type"],
    )
    form = form_cls(data=data, instance=instance)
    if instance:
        form.fields["type"].disabled = True

    return form
