from django import forms

BIND_DISABLED_TYPE_ATTRS = {"x-bind:disabled": "type !== '{key}'"}
BIND_REQUIRED_TYPE_ATTRS = {"x-bind:required": "type === '{key}'", **BIND_DISABLED_TYPE_ATTRS}


def _format_attrs(attrs, key):
    return {name: value.format(key=key) for name, value in attrs.items()}


class OptionalForm(forms.Form):
    """This class is used as a base class for forms that will be used by the 'combined_object_form.html' template.
    It adds the 'x-bind:required' attribute to required form fields so that they are only marked required when
    they are visible."""

    type_key = None

    def __init__(self, *args, **kwargs):
        assert self.type_key is not None, "type_key must be set on OptionalForm subclasses"
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if field.required:
                field.widget.attrs = _format_attrs(BIND_REQUIRED_TYPE_ATTRS, self.type_key)
            else:
                field.widget.attrs = _format_attrs(BIND_DISABLED_TYPE_ATTRS, self.type_key)

    def save(self, instance):
        raise NotImplementedError
