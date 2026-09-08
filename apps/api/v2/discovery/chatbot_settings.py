"""The values a chatbot's settings accept, read straight off ``ChatbotSettingsForm``.

The settings UI and `/chatbot/options/` therefore offer one team the same values: a field the form
scopes to the team or hides behind a feature flag is scoped and hidden the same way here, and a
settings field that gains a choice list reaches the API the moment it reaches the form. Nothing in
this module names a settings field, so keeping the two in sync costs no maintenance.
"""

from collections.abc import Iterable
from typing import Any

from django import forms

from apps.chatbots.forms import ChatbotSettingsForm
from apps.experiments.models import SyntheticVoice


def chatbot_setting_options(request) -> dict[str, list[dict]]:
    """Every settings field that accepts a fixed set of values, with the values this team may write.

    Keyed by the field name the form (and the model behind it) uses. Free-text and boolean fields
    accept anything of their type, so they constrain nothing and are left out.

    ``request`` is the API request: the form reads ``team`` off it to scope each list, and the voice
    feature flag is a team flag, so it resolves against the same team.
    """
    form = ChatbotSettingsForm(request)
    options = ((name, _options_for(field)) for name, field in form.fields.items())
    return {name: entries for name, entries in options if entries is not None}


def _options_for(field: forms.Field) -> list[dict] | None:
    """The field's selectable values, or ``None`` where the field constrains nothing."""
    if isinstance(field, forms.ModelChoiceField):
        # Checked before ChoiceField, which it subclasses. Its `choices` would render the same
        # objects as label strings, losing the ids and the pairing attributes below.
        return [_resource_option(obj) for obj in field.queryset]
    if isinstance(field, forms.ChoiceField):
        return [{"value": value, "label": str(label)} for value, label in _flat_choices(field.choices)]
    return None


def _flat_choices(choices: Iterable) -> Iterable[tuple[Any, Any]]:
    """The field's choices with option groups flattened and the "nothing chosen" entry dropped --
    it is the absence of a value, not one a client can write."""
    for value, label in choices:
        if isinstance(label, list | tuple):  # an option group: (group name, [(value, label), ...])
            yield from ((sub_value, sub_label) for sub_value, sub_label in label if sub_value not in ("", None))
        elif value not in ("", None):
            yield value, label


def _resource_option(obj) -> dict:
    """One of the team's stored resources: the id to write, the name a human reads, and whatever a
    client needs to pair it with another setting."""
    option = {"value": obj.pk, "label": str(obj)}
    if isinstance(obj, SyntheticVoice):
        # A voice names its service rather than carrying a provider's `type`, and belongs either to
        # one provider or to none -- a shared voice any provider of the same type can speak.
        return option | {"type": obj.service.lower(), "provider_id": obj.voice_provider_id}
    provider_type = getattr(obj, "type", None)
    if isinstance(provider_type, str):
        # Providers carry the type a voice or a trace destination must match.
        return option | {"type": provider_type}
    return option
