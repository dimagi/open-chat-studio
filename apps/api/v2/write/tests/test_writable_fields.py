"""The write surface must keep pace with two things it is defined against.

PATCH /chatbots/{id}/ is meant to be the API twin of the chatbot settings page, and also to cover
everything a published version snapshots. Those are two different sets that happen to coincide
today, and a new field can be added to either one alone -- so each is asserted separately, and a
failure here says which of the two drifted.

Both are compared as *model* attribute names: `_writable_model_fields` reads each field's `source`,
which is where the API's `<resource>_id` names map back to the FK they write.
"""

from rest_framework import serializers

from apps.api.v2.write.serializers import ChatbotWriteSerializer
from apps.chatbots.forms import ChatbotSettingsForm
from apps.experiments.models import Experiment


def _writable_model_fields(serializer) -> set[str]:
    """Every model attribute the serializer can write, following nesting and `source`."""
    covered = set()
    for field in serializer.fields.values():
        if field.read_only:
            continue
        if isinstance(field, serializers.BaseSerializer):
            covered |= _writable_model_fields(field)
        else:
            covered.add(field.source)
    return covered


def test_patch_covers_every_versioned_field_except_the_pipeline():
    """`VERSIONED_CONTENT_FIELDS` is what a published version snapshots. Everything in it except
    `pipeline` -- which has its own façade at /chatbots/{id}/pipeline/* -- is writable here, so a
    new versioned field has to be an explicit decision rather than an omission."""
    assert _writable_model_fields(ChatbotWriteSerializer()) == (Experiment.VERSIONED_CONTENT_FIELDS - {"pipeline"})


def test_patch_writes_exactly_what_the_settings_form_edits():
    """The write API mimics the UI's own form rather than the inspect response: inspecting and
    editing are different jobs, and inspect returns plenty that is not writable. A field added to
    the settings page therefore has to be added here too."""
    assert _writable_model_fields(ChatbotWriteSerializer()) == set(ChatbotSettingsForm.Meta.fields)
