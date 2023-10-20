import dataclasses

from django import forms

from apps.generics.exceptions import CombinedFormError


@dataclasses.dataclass
class CombinedForms:
    """Helper class for instances where you have a main form and a list of secondary forms.
    Only one of the secondary forms will be 'active' based on a field in the primary form.

    This usually happens when you have a generic model that stores some 'configuration'. The
    specifics of the configuration is dependent on a 'type' field in the model.
    """

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

    def get_secondary_key(self, instance):
        if instance:
            return getattr(instance, self.secondary_key_field)

    def __post_init__(self):
        if self.secondary_key_field not in self.primary.fields:
            raise CombinedFormError(f"secondary_key_field ('{self.secondary}') must be a field in the primary form")

        field = self.primary.fields[self.secondary_key_field]
        choices = {choice[0] for choice in getattr(field, "choices", []) if choice[0]}
        missing_choices = set(self.secondary) - choices
        if missing_choices:
            raise CombinedFormError(f"No secondary form configured for choices: {missing_choices}")

        missing_secondary = choices - set(self.secondary)
        if missing_secondary:
            raise CombinedFormError(f"Missing secondary forms for choices: {missing_secondary}")
