import json

from django import forms
from django.core.exceptions import ValidationError
from pydantic import TypeAdapter

from apps.evaluations.field_definitions import (
    ChoiceFieldDefinition,
    FieldDefinition,
    FloatFieldDefinition,
    IntFieldDefinition,
    StringFieldDefinition,
)

from .models import AnnotationQueue


class AnnotationQueueForm(forms.ModelForm):
    schema = forms.CharField(
        widget=forms.HiddenInput(),
        required=False,
    )

    class Meta:
        model = AnnotationQueue
        fields = ["name", "description", "schema", "num_reviews_required"]
        widgets = {
            "description": forms.TextInput(attrs={"placeholder": "Optional description"}),
            "num_reviews_required": forms.NumberInput(attrs={"min": 1, "max": 10}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._schema_locked = self.instance.pk and self.instance.items.filter(review_count__gt=0).exists()
        if self._schema_locked:
            self.fields["num_reviews_required"].disabled = True
            self.fields["num_reviews_required"].help_text = "Cannot change after annotations have started."

    def clean_schema(self):
        raw = self.cleaned_data["schema"]
        if not raw:
            raise ValidationError("Schema must have at least one field")

        if isinstance(raw, dict):
            data = raw
        else:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                raise ValidationError(f"Invalid JSON: {e}") from e

        if not isinstance(data, dict):
            raise ValidationError("Schema must be a JSON object (dict)")

        if not data:
            raise ValidationError("Schema must have at least one field")

        adapter = TypeAdapter(FieldDefinition)
        for name, defn in data.items():
            try:
                adapter.validate_python(defn)
            except Exception as e:
                raise ValidationError(f"Invalid field '{name}': {e}") from e

        if self._schema_locked:
            self._validate_locked_schema_change(data)

        return data

    def _validate_locked_schema_change(self, new_schema):
        """When annotations exist, only the 'required' property may change."""
        existing = self.instance.schema

        if set(new_schema.keys()) != set(existing.keys()):
            raise ValidationError("Cannot add or remove fields after annotations have started.")

        for name in new_schema:
            new_def = {k: v for k, v in new_schema[name].items() if k != "required"}
            old_def = {k: v for k, v in existing[name].items() if k != "required"}
            if new_def != old_def:
                raise ValidationError(f"Cannot change field '{name}' structure after annotations have started.")

    def clean_num_reviews_required(self):
        value = self.cleaned_data["num_reviews_required"]
        if not (1 <= value <= 10):
            raise ValidationError("Must be between 1 and 10")
        return value


def build_annotation_form(queue):
    """Dynamically build a Django form from an AnnotationQueue's field definitions."""
    field_defs = queue.get_field_definitions()
    form_fields = {}

    for name, defn in field_defs.items():
        if isinstance(defn, IntFieldDefinition):
            kwargs = {"label": name, "help_text": defn.description, "required": defn.required}
            if defn.ge is not None:
                kwargs["min_value"] = defn.ge
            if defn.le is not None:
                kwargs["max_value"] = defn.le
            form_fields[name] = forms.IntegerField(**kwargs)

        elif isinstance(defn, FloatFieldDefinition):
            kwargs = {"label": name, "help_text": defn.description, "required": defn.required}
            if defn.ge is not None:
                kwargs["min_value"] = defn.ge
            if defn.le is not None:
                kwargs["max_value"] = defn.le
            form_fields[name] = forms.FloatField(**kwargs)

        elif isinstance(defn, ChoiceFieldDefinition):
            choices = [("", "---")] + [(c, c) for c in defn.choices]
            form_fields[name] = forms.ChoiceField(
                label=name,
                help_text=defn.description,
                choices=choices,
                required=defn.required,
            )

        elif isinstance(defn, StringFieldDefinition):
            kwargs = {"label": name, "help_text": defn.description, "required": defn.required}
            if defn.max_length:
                kwargs["max_length"] = defn.max_length
            form_fields[name] = forms.CharField(
                widget=forms.Textarea(attrs={"rows": 3}),
                **kwargs,
            )

    return type("AnnotationForm", (forms.Form,), form_fields)
