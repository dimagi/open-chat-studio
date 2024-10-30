import json

import yaml
from django.core.exceptions import ValidationError
from django.forms import Textarea
from django.forms.fields import CharField, InvalidJSONInput, JSONString


class JSONORYAMLField(CharField):
    """A field that accepts JSON or YAML input and returns a Python object."""

    default_error_messages = {
        "invalid": "Enter a valid JSON or YAML.",
    }
    widget = Textarea

    def __init__(self, encoder=None, decoder=None, **kwargs):
        self.encoder = encoder
        self.decoder = decoder
        super().__init__(**kwargs)

    def to_python(self, value):
        if self.disabled:
            return value
        if value in self.empty_values:
            return None
        elif isinstance(value, list | dict | int | float | JSONString):
            return value
        try:
            if value.strip().startswith(("{", "[")):
                converted = json.loads(value, cls=self.decoder)
            else:
                converted = yaml.safe_load(value)
        except (json.JSONDecodeError, yaml.YAMLError):
            raise ValidationError(
                self.error_messages["invalid"],
                code="invalid",
                params={"value": value},
            )
        if isinstance(converted, str):
            return JSONString(converted)
        else:
            return converted

    def bound_data(self, data, initial):
        if self.disabled:
            return initial
        if data is None:
            return None
        try:
            if data.strip().startswith(("{", "[")):
                return json.loads(data, cls=self.decoder)
            else:
                return yaml.safe_load(data)
        except (json.JSONDecodeError, yaml.YAMLError):
            return InvalidJSONInput(data)

    def prepare_value(self, value):
        if isinstance(value, InvalidJSONInput):
            return value
        return json.dumps(value, ensure_ascii=False, cls=self.encoder, indent=2)

    def has_changed(self, initial, data):
        if super().has_changed(initial, data):
            return True
        # For purposes of seeing whether something has changed, True isn't the
        # same as 1 and the order of keys doesn't matter.
        return json.dumps(initial, sort_keys=True, cls=self.encoder) != json.dumps(
            self.to_python(data), sort_keys=True, cls=self.encoder
        )
