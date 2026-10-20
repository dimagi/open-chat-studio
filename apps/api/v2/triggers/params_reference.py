"""The ``params`` each trigger action takes, as API documentation.

Built from ``ACTION_PARAMS_FORMS``, the forms that validate the params, so the documentation lists
exactly the keys and constraints the server enforces.
"""

from django import forms

from apps.events.forms import ACTION_PARAMS_FORMS

#: Params the server fills in. A request that sends one is refused.
SERVER_SET_PARAMS = ("experiment_id",)

#: Rules a form enforces in code rather than in a field's declared constraints.
NOTES = {
    ("send_message_to_bot", "message_to_bot"): "Left out or empty, a default prompt is used.",
    ("pipeline_start", "pipeline_id"): (
        "One of the `trigger_pipeline` entries the `chatbot_options` endpoint lists: a pipeline in this "
        "team that is not a chatbot's own pipeline. Any other id is refused with `400`."
    ),
}


def action_params_reference() -> str:
    """A Markdown table of the params each action type takes."""
    rows = ["| `action_type` | `params` |", "| --- | --- |"]
    for action_type, form_class in ACTION_PARAMS_FORMS.items():
        fields = [
            _describe(action_type, name, field)
            for name, field in form_class.base_fields.items()
            if name not in SERVER_SET_PARAMS
        ]
        rows.append(f"| `{action_type}` | {'<br>'.join(fields) or '`{}`'} |")
    return "\n".join(rows)


def _describe(action_type: str, name: str, field: forms.Field) -> str:
    constraints = [_type_of(field), "required" if field.required else "optional"]
    if (minimum := getattr(field, "min_value", None)) is not None:
        constraints.append(f"at least {minimum}")
    notes = [str(text).rstrip(".") + "." for text in (field.help_text, NOTES.get((action_type, name))) if text]
    return " ".join([f"`{name}` ({', '.join(constraints)})", *notes])


def _type_of(field: forms.Field) -> str:
    if isinstance(field, forms.ModelChoiceField):
        return "integer id"
    if isinstance(field, forms.ChoiceField):
        return "one of " + " / ".join(f"`{value}`" for value, _label in field.choices)
    if isinstance(field, forms.IntegerField):
        return "integer"
    return "string"
