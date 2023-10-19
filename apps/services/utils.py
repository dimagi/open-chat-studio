import dataclasses

from django import forms

from apps.services.models import ServiceConfig, ServiceType


@dataclasses.dataclass
class CombinedForms:
    primary: forms.BaseModelForm
    secondary: dict[forms.Form]
    # name of the field in the primary form which determines which secondary form is shown
    secondary_key_field: str

    def active_secondary(self):
        assert self.primary.is_valid(), "primary form must be valid to get active secondary form"
        return self.secondary[self.primary.cleaned_data[self.secondary_key_field]]

    def is_valid(self):
        if not self.primary.is_valid():
            return False

        return self.active_secondary().is_valid()

    def save(self):
        assert self.is_valid(), "combined form must be valid to save"
        instance = self.primary.save(commit=False)
        self.active_secondary().save(instance)
        return instance


def get_service_forms(service_type: ServiceType, data=None) -> CombinedForms:
    return CombinedForms(
        primary=_get_main_form(service_type, data=data.copy() if data else None),
        secondary={subtype: subtype.form_cls(data=data.copy() if data else None) for subtype in service_type.subtype},
        secondary_key_field="subtype",
    )


def get_service_form(subtype, instance=None, data=None):
    main_form = _get_main_form(instance.service_type, instance=instance, data=data)
    initial_config = instance.config if instance else None
    config_form = subtype.form_cls(data=data, initial=initial_config)
    return [main_form, config_form]


def _get_main_form(service_type, instance=None, data=None):
    widgets = {
        "service_type": forms.HiddenInput(),
    }
    if instance:
        widgets["subtype"] = forms.HiddenInput()
    return forms.modelform_factory(
        ServiceConfig,
        fields=["service_type", "name", "subtype"],
        labels={
            "subtype": "Type",
        },
        widgets=widgets,
    )(data=data, instance=instance, initial={"service_type": service_type})
