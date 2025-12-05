import dataclasses

from django import forms

from apps.generics.exceptions import TypeSelectFormError

BIND_DISABLED_ATTRS = {"x-bind:disabled": "type !== '{key}'"}
BIND_REQUIRED_ATTRS = {"x-bind:required": "type === '{key}'"}
BIND_REQUIRED_DISABLED_ATTRS = {**BIND_REQUIRED_ATTRS, **BIND_DISABLED_ATTRS}


@dataclasses.dataclass
class TypeSelectForm:
    """Helper class for instances where you have a main form and a list of secondary forms.
    Only one of the secondary forms will be 'active' based on a 'type' field in the primary form.

    This usually happens when you have a generic model that stores some 'configuration'. The
    specifics of the configuration is dependent on a 'type' field in the model.

    Example:

        TypeSelectForm(
            primary=modelform_factory(MyModel, fields=["name", "the_type"]),
            secondary={
                "typeA": TypeAForm,
                "typeB": TypeBForm,
            },
            secondary_key_field="the_type",
        )
    """

    primary: forms.BaseModelForm
    secondary: dict[str, forms.Form]
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
        assert self.is_valid(), "form must be valid to save"
        instance = self.primary.save(commit=False)
        self.active_secondary().save(instance)
        return instance

    def get_secondary_key(self, instance):
        if instance:
            return getattr(instance, self.secondary_key_field)
        return self.primary.fields[self.secondary_key_field].choices[0][0]

    def __post_init__(self):
        if self.secondary_key_field not in self.primary.fields:
            raise TypeSelectFormError(f"secondary_key_field ('{self.secondary}') must be a field in the primary form")

        type_field = self.primary.fields[self.secondary_key_field]
        choices = {choice[0] for choice in getattr(type_field, "choices", []) if choice[0]}
        missing_choices = set(self.secondary) - choices
        if missing_choices:
            raise TypeSelectFormError(f"No secondary form configured for choices: {missing_choices}")

        missing_secondary = choices - set(self.secondary)
        if missing_secondary:
            raise TypeSelectFormError(f"Missing secondary forms for choices: {missing_secondary}")

        type_field.widget.attrs.update({"x-model": "type"})

        for key, form in self.secondary.items():
            apply_alpine_attrs(form, key)


def apply_alpine_attrs(form, key):
    """This adds the 'x-bind:required' attribute to required form fields so that they are only marked required when
    they are visible.

    It also adss 'x-bind:disabled' to all fields to prevent them from being included in the form submission if they
    are not visible.
    """
    for field in form.fields.values():
        if field.required:
            field.widget.attrs.update(_format_attrs(BIND_REQUIRED_DISABLED_ATTRS, key))
        else:
            field.widget.attrs.update(_format_attrs(BIND_DISABLED_ATTRS, key))


def _format_attrs(attrs, key):
    return {name: value.format(key=key) for name, value in attrs.items()}
